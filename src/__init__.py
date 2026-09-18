# Pellaeon: plain-English control of UCSF ChimeraX.
# Bundle entry point. Everything ChimeraX-specific stays in tool.py / bridge.py /
# cmd.py; the "core" package is plain Python and can be unit-tested anywhere.
__version__ = "0.1.0"

from chimerax.core.toolshed import BundleAPI


class _PellaeonAPI(BundleAPI):
    api_version = 1

    @staticmethod
    def start_tool(session, bi, ti):
        if ti.name == "Pellaeon":
            from .tool import PellaeonTool
            return PellaeonTool(session, ti.name)
        raise ValueError("unknown tool: %s" % ti.name)

    @staticmethod
    def get_class(class_name):
        if class_name == "PellaeonTool":
            from .tool import PellaeonTool
            return PellaeonTool
        raise ValueError("unknown class: %s" % class_name)

    @staticmethod
    def register_command(bi, ci, logger):
        from . import cxcommand as cmd
        cmd.register(ci.name, logger)


bundle_api = _PellaeonAPI()
