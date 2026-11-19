"""ComfyUI-DirectorStudio: H3 Director Studio served inside ComfyUI at  /director

Install: put this folder in ComfyUI/custom_nodes/ and restart ComfyUI. Then open your
usual ComfyUI address with /director at the end (same port, same Vast link).
No nodes are added to the graph; no models are downloaded.
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path: sys.path.insert(0, _HERE)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = './web_ext'

try:                      # only the CPU cores this container really gets (Vast: 46 of 384 visible -> everything crawled)
    import studio_threads_v0918 as _thr
    _thr.apply(); print('[DirectorStudio] ' + _thr.describe())
except Exception as _e: print('[DirectorStudio] CPU thread cap skipped:', type(_e).__name__, _e)

try:
    from server import PromptServer
    import folder_paths
    import studio_routes_v080 as _routes
    _input = folder_paths.get_input_directory()
    _output = folder_paths.get_output_directory()
    _routes.register(PromptServer.instance.routes,
                     projects_root=os.environ.get('DIRECTOR_STUDIO_ROOT', '/workspace/DirectorStudio'),
                     comfy_root=os.path.dirname(_input), input_dir=_input, output_dir=_output,
                     port_fn=lambda: getattr(PromptServer.instance, 'port', 8188))
    print('[DirectorStudio] ready at /director')
    try:                  # public demo: ComfyUI runs hidden behind the Director
        import studio_demo_v0970 as _demo
        if _demo.active():
            _demo.install(PromptServer.instance.app)
            print('[DirectorStudio] demo: the ComfyUI address opens /director (ComfyUI itself: /?comfy=1)')
    except Exception as _e: print('[DirectorStudio] demo front door skipped:', type(_e).__name__, _e)
except Exception as _e:   # never break ComfyUI startup
    print('[DirectorStudio] not loaded:', type(_e).__name__, _e)

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']
