"""Offline regressions; no ROS connection or robot motion."""
import ast
import asyncio
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULES = Path(__file__).resolve().parents[1] / 'modules'
sys.path.insert(0, str(MODULES))
from experiment_log import current_run, logged_tool, start_run, write_event, log_dir
from test_summary import motion_completed, summarize


def worker(_):
    @logged_tool('worker')
    def echo(value):
        return value
    echo('parallel')
    return current_run()


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'CARS_LOG_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def records(self, run_id):
        return [json.loads(line) for line in (log_dir() / f'run_{run_id}.jsonl').read_text().splitlines()]

    def test_rotation_during_call_and_duplicate_id(self):
        first = start_run('first', {'scenario': 'video'})

        @logged_tool('test')
        def rotate():
            start_run('second')
            return 'done'

        rotate()
        self.assertEqual([r['event'] for r in self.records(first)], ['run_started', 'tool_start', 'tool_end'])
        self.assertEqual(current_run(), 'second')
        rotate_events = self.records(first)[1:]
        self.assertEqual(rotate_events[0]['call_id'], rotate_events[1]['call_id'])
        with self.assertRaises(FileExistsError):
            start_run('first')
        self.assertEqual(current_run(), 'second')
        write_event({'event': 'after_rotation'})
        self.assertEqual(self.records('second')[-1]['event'], 'after_rotation')

    def test_concurrent_processes_share_initial_run_and_sequence(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(worker, range(24)))
        self.assertEqual(len(set(ids)), 1)
        rows = self.records(ids[0])
        self.assertEqual(len(rows), 49)
        self.assertEqual([r['seq'] for r in rows], list(range(1, 50)))
        starts = {r['call_id'] for r in rows if r['event'] == 'tool_start'}
        ends = {r['call_id'] for r in rows if r['event'] == 'tool_end'}
        self.assertEqual(starts, ends)
        self.assertEqual(len(starts), 24)

    def test_exception_async_and_signature(self):
        @logged_tool('test')
        async def failure(value: int = 4):
            raise ValueError('sensor failure')
        self.assertEqual(inspect.signature(failure).parameters['value'].default, 4)
        with self.assertRaisesRegex(ValueError, 'sensor failure'):
            asyncio.run(failure())
        rows = self.records(current_run())
        self.assertEqual(rows[1]['girdi'], {'value': 4})
        self.assertEqual(rows[2]['status'], 'exception')
        self.assertEqual(rows[2]['error']['type'], 'ValueError')

    def test_invalid_marker_and_id(self):
        for value in ('', '../escape', '/tmp/run', 'a.b', 'x' * 97):
            with self.assertRaises(ValueError):
                start_run(value)
        (log_dir() / 'current_run.txt').write_text('../escape')
        with self.assertRaises(ValueError):
            current_run()

    def test_cli_from_another_directory_updates_existing_wrapper(self):
        import subprocess
        @logged_tool('test')
        def existing_server_tool():
            return 'ok'
        first = start_run('before_cli')
        existing_server_tool()
        command = [sys.executable, str(MODULES / 'experiment_log.py'),
                   '--run-id', 'from_cli', '--metadata', '{"scenario":"video"}']
        result = subprocess.run(command, cwd=self.temp.name, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'from_cli')
        existing_server_tool()
        self.assertEqual(len(self.records(first)), 3)
        self.assertEqual(len(self.records('from_cli')), 3)
        self.assertEqual(self.records('from_cli')[0]['metadata']['scenario'], 'video')
        duplicate = subprocess.run(command, cwd=self.temp.name, capture_output=True, text=True)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(current_run(), 'from_cli')

    def test_log_failure_prevents_unrecorded_execution(self):
        called = []
        @logged_tool('test')
        def action():
            called.append(True)
        with patch('experiment_log.write_event', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                action()
        self.assertEqual(called, [])

    def test_all_registered_tools_are_decorated(self):
        total = 0
        for path in MODULES.glob('*_mcp.py'):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.FunctionDef) and any(ast.unparse(d) == 'mcp.tool()' for d in node.decorator_list):
                    self.assertEqual(ast.unparse(node.decorator_list[0]), 'mcp.tool()')
                    self.assertEqual(node.decorator_list[1].func.id, 'logged_tool', node.name)
                    total += 1
        self.assertEqual(total, 18)

    def test_actual_tools_over_mcp_with_fake_ros(self):
        # Real FastMCP registration/transport and real tools; only ROS is fake.
        # Physical __file__ paths point into a disposable copy, isolating memory.
        import shutil
        from types import SimpleNamespace
        from fastmcp import Client

        class Ros:
            is_connected = False
            def __init__(self, **kwargs):
                pass
            def run(self):
                raise ConnectionError('offline test')

        class Topic:
            def __init__(self, *args, **kwargs):
                pass
            def subscribe(self, callback):
                pass
            def publish(self, message):
                raise AssertionError('Offline tests must not publish motion')

        copied = Path(self.temp.name) / 'runtime/modules'
        shutil.copytree(MODULES, copied)
        modules = {}
        ros_patch = patch.dict(sys.modules, {'roslibpy': SimpleNamespace(Ros=Ros, Topic=Topic, Message=dict)})
        ros_patch.start()
        self.addCleanup(ros_patch.stop)
        for path in copied.glob('*_mcp.py'):
            spec = importlib.util.spec_from_file_location('offline_' + path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[path.stem] = module

        async def invoke_all():
            names = []
            for module in modules.values():
                async with Client(module.mcp) as client:
                    for tool in await client.list_tools():
                        properties = tool.inputSchema.get('properties', {})
                        arguments = {name: 0.0 if prop.get('type') in ('number', 'integer') else 'test'
                                     for name, prop in properties.items()}
                        result = await client.call_tool(tool.name, arguments)
                        self.assertFalse(result.is_error, tool.name)
                        names.append(tool.name)
            return names

        names = asyncio.run(invoke_all())
        rows = self.records(current_run())
        starts = [r for r in rows if r['event'] == 'tool_start']
        ends = [r for r in rows if r['event'] == 'tool_end']
        self.assertEqual(len(names), 18)
        self.assertEqual([r['tool'] for r in starts], names)
        self.assertEqual([r['call_id'] for r in starts], [r['call_id'] for r in ends])
        self.assertTrue(all(r['status'] == 'returned' for r in ends))
        self.assertTrue(any('HATA' in str(r['cikti_ozet']) for r in ends))

        # Exercise the video chain with successful observations and persistence.
        run_id = start_run('critical_chain')
        camera = modules['camera_mcp']
        lidar = modules['lidar_mcp']
        autopilot = modules['autopilot_mcp']
        connected = SimpleNamespace(is_connected=True)
        camera.ros_client = lidar.ros_client = autopilot.ros_client = connected

        class CameraTopic:
            def __init__(self, *args, **kwargs):
                pass
            def subscribe(self, callback):
                callback({'data': 'YWJj'})
            def unsubscribe(self):
                pass

        camera.roslibpy = SimpleNamespace(Topic=CameraTopic)
        published = []
        autopilot.cmd_vel_pub = SimpleNamespace(publish=published.append)

        def fresh_scan(_=None):
            import time
            lidar.latest_scan_ranges = autopilot.latest_scan_ranges = [2.0] * 360
            lidar.son_lidar_guncelleme = autopilot.son_lidar_guncelleme = time.time()
            autopilot.son_odom_guncelleme = time.time()

        fresh_scan()
        chain = [
            ('camera_mcp', 'hizli_kamera_al', {}),
            ('lidar_mcp', 'anlik_lidar_radar_haritasi_al', {}),
            ('autopilot_mcp', 'lazer_mesafe_olc', {'hedef_aci': 20.0}),
            ('custom_memory_mcp', 'ltm_kalici_kayit_yap', {
                'aktif_milestone_adi': 'room', 'baglanacagi_eski_milestone': 'start',
                'gecis_iliskisi': 'door', 'obje_adi': 'chair', 'obje_x': 2.0, 'obje_y': 0.0,
                'robot_x': 0.0, 'robot_y': 0.0, 'robot_yaw': 0.0,
                'iliskili_obje': 'Yok', 'iliski_tanimi': 'Yok', 'ozellikler': 'red'}),
            ('custom_memory_mcp', 'ltm_oku', {}),
            ('autopilot_mcp', 'kognitif_rota_planla', {'baslangic_milestone': 'start', 'hedef_milestone': 'room'}),
            ('autopilot_mcp', 'hedef_objeye_yonelme_hesapla', {'oda_adi': 'room', 'obje_adi': 'chair'}),
            ('autopilot_mcp', 'otopilot_kademeli_hareket', {'hedef_aci_derece': 0.0, 'hedef_mesafe_metre': 0.0}),
            ('custom_memory_mcp', 'stm_anlik_durum_guncelle', {'son_aksiyon': 'stop', 'delta_sonuc': 'ok', 'lokal_durum': 'clear', 'niyet': 'observe'}),
            ('custom_memory_mcp', 'stm_oku', {}),
            ('kartograf_mcp', 'harita_guncelle', {'mermaid_kodu': 'graph TD\n start --> room'}),
        ]

        async def invoke_chain():
            for module_name, name, arguments in chain:
                async with Client(modules[module_name].mcp) as client:
                    result = await client.call_tool(name, arguments)
                    self.assertFalse(result.is_error, name)

        with patch('time.sleep', side_effect=fresh_scan):
            asyncio.run(invoke_chain())
        rows = self.records(run_id)
        self.assertEqual([r['tool'] for r in rows if r['event'] == 'tool_start'], [c[1] for c in chain])
        ends = [r for r in rows if r['event'] == 'tool_end']
        self.assertEqual(len(ends), len(chain))
        self.assertTrue(all(r['status'] == 'returned' and 'HATA' not in str(r['cikti_ozet']) for r in ends))
        self.assertTrue(all(m['linear']['x'] == 0 and m['angular']['z'] == 0 for m in published))
        self.assertIn('room', Path(modules['kartograf_mcp'].MAP_FILE_PATH).read_text())
        self.assertEqual(json.loads(Path(modules['custom_memory_mcp'].STM_FILE).read_text())[-1]['niyet'], 'observe')

    def test_image_summary_excludes_base64(self):
        from mcp.types import ImageContent
        @logged_tool('camera')
        def camera():
            return [ImageContent(type='image', mimeType='image/jpeg', data='YWJj')]
        result = camera()
        self.assertEqual(result[0].data, 'YWJj')
        image = self.records(current_run())[-1]['cikti_ozet'][0]
        self.assertNotIn('data', image)
        self.assertEqual(image['base64_length'], 4)
        self.assertEqual(len(image['sha256_base64']), 64)


class SummaryTests(unittest.TestCase):
    def test_status_precedence_and_counts(self):
        for statuses, expected in ((['passed'], 0), (['passed', 'skipped'], 3),
                                   (['failed', 'skipped'], 1), ([], 3)):
            result = summarize([{'status': status} for status in statuses])
            self.assertEqual(result['exit_code'], expected)
            self.assertEqual(result['total'], len(statuses))

    def test_successful_turn_does_not_hide_translation_timeout(self):
        report = '- Dönüş: İstenen 0° (Gerçekleşen: 0°) başarıyla tamamlandı.\n⏱️ MESAFE TIMEOUT'
        self.assertTrue(motion_completed(report, 'Dönüş'))
        self.assertFalse(motion_completed(report, 'İlerleme'))


if __name__ == '__main__':
    unittest.main()
