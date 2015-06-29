# -*- coding: utf-8 -*-

from nimp.commands._command     import *
from nimp.utilities.perforce    import *
from nimp.utilities.file_mapper import *

FARM_P4_PORT     = "farmproxy:1666"
FARM_P4_USER     = "CIS-CodeBuilder"
FARM_P4_PASSWORD = "CIS-CodeBuilder"

#-------------------------------------------------------------------------------
class PerforceCommand(Command):
    def __init__(self):
        Command.__init__(self, 'perforce', 'Perforce tasks')

    #---------------------------------------------------------------------------
    def configure_arguments(self, env, parser):
        subparsers  = parser.add_subparsers(title='P4 Commands')
        self._register_prepare_workspace(subparsers)
        self._register_clean_workspace(subparsers)
        self._register_checkout(subparsers)
        self._register_reconcile(subparsers)
        self._register_submit(subparsers)
        return True

    #---------------------------------------------------------------------------
    def run(self, env):
        if(hasattr(env, 'arg') and env.arg is not None):
            for key_value in env.arg:
                setattr(env, key_value[0], key_value[1])
        env.standardize_names()
        if not hasattr(env, 'p4_command_to_run'):
            log_error(log_prefix() + "No P4 command specified. Please try nimp perforce -h to get a list of available commands")
            return False
        return env.p4_command_to_run(env)

    #---------------------------------------------------------------------------
    def _register_prepare_workspace(self, subparsers):
        def _execute(env):
            if not p4_create_config_file(FARM_P4_PORT, FARM_P4_USER, FARM_P4_PASSWORD, env.p4_client):
                return False

            if not p4_clean_workspace():
                return False

            if env.patch_config is not None and env.patch_config != "None":
                if not env.load_config_file(env.patch_config):
                    log_error("Error while loading patch config file {0}, aborting...", env.patch_config)
                    return False

                for file_path, revision in env.patch_files_revisions:
                    log_notification("Syncing file {0} to revision {1}", file_path, revision)
                    if not p4_sync(file_path, revision):
                        return False

                    if file_path == ".nimp.conf":
                        log_notification("Reloading config...")
                        if not env.load_config_file(".nimp.conf"):
                            return False
            return True

        parser = subparsers.add_parser("prepare-workspace", help = "Writes a .P4CONFIG file and removes all pending CLs from workspace")
        parser.add_argument('p4_client', metavar = '<CLIENT_NAME>', type = str)
        parser.add_argument("--patch-config",
                            help = "Path to the patch config file",
                            metavar = "<FILE>",
                            default = "None")
        parser.set_defaults(p4_command_to_run = _execute)

    #---------------------------------------------------------------------------
    def _register_clean_workspace(self, subparsers):
        def _execute(env):
            return p4_clean_workspace()

        parser = subparsers.add_parser("clean-workspace", help = "Reverts and delete all pending changelists.")
        parser.set_defaults(p4_command_to_run = _execute)


    #---------------------------------------------------------------------------
    def _register_checkout(self, subparsers):
        _register_file_set_command(subparsers , "checkout", "Checks out a file set", p4_edit)

    #---------------------------------------------------------------------------
    def _register_reconcile(self, subparsers):
        _register_file_set_command(subparsers, "reconcile", "Reconciles a file set", p4_reconcile)

    #---------------------------------------------------------------------------
    def _register_submit(self, subparsers):
        def _execute(env):
            cl_number = p4_get_or_create_changelist(env.format(env.cl_name))

            if cl_number is None:
                return False

            return p4_submit(cl_number)

        parser = subparsers.add_parser("submit",
                                       help = "Reconciles a file set")
        parser.add_argument('cl_name', metavar = '<FORMAT>', type = str)
        parser.set_defaults(p4_command_to_run = _execute)

#---------------------------------------------------------------------------
def _register_file_set_command(subparsers, command_name, help, p4_func):
    def _execute(env):
        cl_number = p4_get_or_create_changelist(env.format(env.cl_name))

        if cl_number is None:
            return False

        if env.file_set:
            files = env.map_files()
            if files.load_set(env.format(env.p4_path)) is None:
                return False
            files = [file[0] for file in files()]
        else:
            files = [env.p4_path]

        return p4_func(cl_number, *files)
    parser = subparsers.add_parser(command_name,
                                    help = help)
    parser.add_argument('cl_name', metavar = '<STR>', type = str)
    parser.add_argument('p4_path', metavar = '<PATH>', type = str)
    parser.add_argument('--file-set',
                        help    = "Handle path as a file set, not a regular path.",
                        action  = "store_true",
                        default = False)
    parser.add_argument('--arg',
                        help    = 'Specify interpolation arguments to set while checking out.',
                        nargs=2,
                        action='append',
                        default = [])
    parser.set_defaults(p4_command_to_run = _execute)