# -*- coding: utf-8 -*-
# Copyright © 2014—2018 Dontnod Entertainment

# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# 'Software'), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:

# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
''' Commands related to version packaging '''

import logging
import os
import re
import shutil

import nimp.commands
import nimp.environment
import nimp.system
import nimp.sys.process


def get_ini_value(file_path, key):
    ''' Retrieves a value from a ini file '''
    file_path = nimp.system.sanitize_path(file_path)
    with open(file_path) as ini_file:
        ini_content = ini_file.read()
    match = re.search('^' + key + r'=(?P<value>.*?)$', ini_content, re.MULTILINE)
    if not match:
        raise KeyError('Key {key} was not found in {file_path}'.format(**locals()))
    return match.group('value')


class Package(nimp.command.Command):
    ''' Packages an unreal project for release '''
    def __init__(self):
        super(Package, self).__init__()


    def configure_arguments(self, env, parser):
        nimp.command.add_common_arguments(parser, 'configuration', 'platform', 'revision')

        command_steps = [ 'initialize', 'cook', 'stage', 'package' ]
        parser.add_argument('--steps', help = 'Only run specified steps instead of all of them',
                            choices = command_steps, default = command_steps, nargs = '+')
        parser.add_argument('--target', help = 'Set the target configuration to use')
        parser.add_argument('--layout', help = 'Set the layout file to use for the package (for consoles)')
        parser.add_argument('--patch', help = 'Create a patch based on the specified release', metavar = '<version>')
        parser.add_argument('--final', help = 'Enable package options for final submission', action = 'store_true')
        parser.add_argument('--iterate', help = 'Enable iterative cooking', action = 'store_true')
        parser.add_argument('--compress', help = 'Enable pak file compression', action = 'store_true')

        return True


    def is_available(self, env):
        return nimp.unreal.is_unreal4_available(env)


    def run(self, env):
        ue4_cmd_platform = 'WindowsNoEditor' if env.ue4_platform == 'Win64' else env.ue4_platform
        engine_directory = env.format('{root_dir}/Engine')
        project_directory = env.format('{root_dir}/{game}')
        stage_directory = env.format('{root_dir}/{game}/Saved/StagedBuilds/' + ue4_cmd_platform)
        package_directory = env.format('{root_dir}/{game}/Saved/Packages/' + ue4_cmd_platform)

        if 'initialize' in env.steps:
            Package._initialize(env)
        if 'cook' in env.steps:
            Package._cook(engine_directory, env.game, ue4_cmd_platform, env.iterate)
        if 'stage' in env.steps:
            Package._stage(engine_directory, project_directory, stage_directory, env.game, env.ue4_platform, env.ue4_config, env.layout, env.compress, env.patch)
        if 'package' in env.steps:
            Package._package_for_platform(env, project_directory, env.game, env.ue4_platform, env.ue4_config, stage_directory, package_directory, env.final)

        return True


    @staticmethod
    def _initialize(env):
        if env.target:
            configuration_fileset = nimp.system.map_files(env)
            configuration_fileset.src('{game}/Config.{target}').to('{root_dir}/{game}/Config').glob('**')
            configuration_success = nimp.system.all_map(nimp.system.robocopy, configuration_fileset())
            if not configuration_success:
                raise RuntimeError('Initialize failed')

        hook_success = nimp.environment.execute_hook('preship', env)
        if not hook_success:
            raise RuntimeError('Initialize failed')


    @staticmethod
    def _cook(engine_directory, project, platform, iterate):
        cook_command = [
            nimp.system.sanitize_path(engine_directory + '/Binaries/Win64/UE4Editor-Cmd.exe'),
            project, '-Run=Cook', '-TargetPlatform=' + platform,
            '-BuildMachine', '-Unattended', '-StdOut', '-UTF8Output',
        ]
        if iterate:
            cook_command += [ '-Iterate', '-IterateHash' ]

        # Heartbeart for background shader compilation and existing cook verification
        cook_success = nimp.sys.process.call(cook_command, heartbeat = 60)
        if cook_success != 0:
            raise RuntimeError('Cook failed')


    @staticmethod
    def _stage(engine_directory, project_directory, stage_directory, project, platform, configuration, layout_file_path, compress, patch):
        stage_command = [
            nimp.system.sanitize_path(engine_directory + '/Binaries/DotNET/AutomationTool.exe'),
            'BuildCookRun', '-UE4exe=UE4Editor-Cmd.exe', '-UTF8Output',
            '-Project=' + project, '-TargetPlatform=' + platform, '-ClientConfig=' + configuration,
            '-SkipCook', '-Stage', '-Pak', '-Prereqs', '-CrashReporter', '-NoDebugInfo',
        ]
        if compress:
            stage_command += [ '-Compressed' ]
        if patch:
            stage_command += [ '-GeneratePatch', '-BasedOnReleaseVersion=' + patch ]

        stage_success = nimp.sys.process.call(stage_command)
        if stage_success != 0:
            raise RuntimeError('Stage failed')

        if platform == 'XboxOne':
            Package._stage_xbox_manifest(project_directory, stage_directory, configuration)
            # Dummy files for empty chunks
            with open(nimp.system.sanitize_path(stage_directory + '/LaunchChunk.bin'), 'w') as empty_file:
                empty_file.write('\0')
            with open(nimp.system.sanitize_path(stage_directory + '/AlignmentChunk.bin'), 'w') as empty_file:
                empty_file.write('\0')

        if layout_file_path:
            for current_configuration in configuration.split('+'):
                layout_file_name = project + '-' + current_configuration + '.' + ('gp4' if platform == 'PS4' else 'xml')
                layout_destination = nimp.system.sanitize_path(stage_directory + '/' + layout_file_name)
                Package._stage_file(layout_file_path, layout_destination, True, platform, current_configuration)

        # Homogenize binary file name for console packaging
        if platform == 'PS4':
            binary_path = nimp.system.sanitize_path(stage_directory + '/' + (project + '/Binaries/PS4/' + project).lower())
            if os.path.exists(binary_path + '.self'):
                shutil.move(binary_path + '.self', binary_path + '-ps4-development.self')
        elif platform == 'XboxOne':
            binary_path = nimp.system.sanitize_path(stage_directory + '/' + project + '/Binaries/XboxOne/' + project)
            if os.path.exists(binary_path + '.exe'):
                shutil.move(binary_path + '.exe', binary_path + '-XboxOne-Development.exe')

        # Copy the release files to have a complete package
        if patch:
            ue4_cmd_platform = 'WindowsNoEditor' if platform == 'Win64' else platform
            release_directory = project_directory + '/Releases/' + patch + '/' + ue4_cmd_platform
            pak_file_name = project + '-' + ue4_cmd_platform + '.pak'
            Package._stage_file(release_directory + '/' + pak_file_name, stage_directory + '/' + project + '/Content/Paks/' + pak_file_name)


    @staticmethod
    def _stage_xbox_manifest(project_directory, stage_directory, configuration):
        os.remove(nimp.system.sanitize_path(stage_directory + '/AppxManifest.xml'))
        os.remove(nimp.system.sanitize_path(stage_directory + '/appdata.bin'))

        manifest_source = project_directory + '/Config/XboxOne/AppxManifest.xml'
        for current_configuration in configuration.split('+'):
            current_stage_directory = stage_directory + '/Manifests/' + current_configuration
            os.makedirs(nimp.system.sanitize_path(current_stage_directory))
            Package._stage_file(manifest_source, current_stage_directory + '/AppxManifest.xml', True, 'XboxOne', current_configuration)

            appdata_command = [
                nimp.system.sanitize_path(os.environ['DurangoXDK'] + '/bin/MakePkg.exe'),
                'appdata',
                '/f', nimp.system.sanitize_path(current_stage_directory +  '/AppxManifest.xml'),
                '/pd', nimp.system.sanitize_path(current_stage_directory),
            ]

            appdata_success = nimp.sys.process.call(appdata_command)
            if appdata_success != 0:
                raise RuntimeError('Stage failed')


    @staticmethod
    def _stage_file(source, destination, apply_transform = False, platform = None, configuration = None):
        source = nimp.system.sanitize_path(source)
        destination = nimp.system.sanitize_path(destination)
        logging.info('Staging %s to %s', source, destination)

        if apply_transform:
            with open(source, 'r') as source_file:
                file_content = source_file.read()
            file_content = file_content.format(configuration = (configuration if platform != 'PS4' else configuration.lower()))
            if configuration == 'Shipping':
                file_content = re.sub(r'<!-- #if Debug -->(.*?)<!-- #endif Debug -->', '', file_content, 0, re.DOTALL)
            with open(destination, 'w') as destination_file:
                destination_file.write(file_content)
        else:
            shutil.copyfile(source, destination)


    @staticmethod
    def _package_for_platform(env, project_directory, project, platform, configuration, source, destination, is_final_submission):
        source = nimp.system.sanitize_path(source)
        destination = nimp.system.sanitize_path(destination)

        if os.path.exists(destination):
            logging.info('Removing %s', destination)
            shutil.rmtree(destination, ignore_errors = True)
        os.makedirs(destination)

        if platform in [ 'Linux', 'Mac', 'Win32', 'Win64' ]:
            package_fileset = nimp.system.map_files(env)
            package_fileset.src(source[ len(env.root_dir) + 1 : ]).to(destination).glob('**')
            package_success = nimp.system.all_map(nimp.system.robocopy, package_fileset())
            if not package_success:
                raise RuntimeError('Package failed')

        elif platform == 'XboxOne':
            package_tool_path = nimp.system.sanitize_path(os.environ['DurangoXDK'] + '/bin/MakePkg.exe')
            game_os = nimp.system.sanitize_path(source + '/era.xvd')
            ini_file_path = nimp.system.sanitize_path(project_directory + '/Config/XboxOne/XboxOneEngine.ini')
            product_id = get_ini_value(ini_file_path, 'ProductId')
            content_id = get_ini_value(ini_file_path, 'ContentId')

            for current_configuration in configuration.split('+'):
                current_destination = nimp.system.sanitize_path(destination + '/' + current_configuration)
                layout_file = nimp.system.sanitize_path(source + '/' + project + '-' + current_configuration + '.xml')
                package_command = [
                    package_tool_path, 'pack', '/v', '/gameos', game_os,
                    '/f', layout_file, '/d', source, '/pd', current_destination,
                    '/productid', product_id, '/contentid', content_id,
                ]

                if is_final_submission:
                    package_command += [ '/l' ]

                os.mkdir(current_destination)
                manifest_file_collection = os.listdir(nimp.system.sanitize_path(source + '/Manifests/' + current_configuration))
                for manifest_file in manifest_file_collection:
                    shutil.copyfile(nimp.system.sanitize_path(source + '/Manifests/' + current_configuration + '/' + manifest_file),
                                    nimp.system.sanitize_path(source + '/' + manifest_file))
                package_success = nimp.sys.process.call(package_command)
                for manifest_file in manifest_file_collection:
                    os.remove(nimp.system.sanitize_path(source + '/' + manifest_file))
                if package_success != 0:
                    raise RuntimeError('Package failed')

        elif platform == 'PS4':
            package_tool_path = nimp.system.sanitize_path(os.environ['SCE_ROOT_DIR'] + '/ORBIS/Tools/Publishing Tools/bin/orbis-pub-cmd.exe')
            temporary_directory = nimp.system.sanitize_path(project_directory + '/Saved/Temp')
            ini_file_path = project_directory + '/Config/PS4/PS4Engine.ini'
            title_id = get_ini_value(ini_file_path, 'TitleID')

            for current_configuration in configuration.split('+'):
                destination_file = nimp.system.sanitize_path(destination + '/' + env.game + '-' + current_configuration + '-' + title_id + '.pkg' )
                layout_file = nimp.system.sanitize_path(source + '/' + project + '-' + current_configuration + '.gp4')
                package_command = [
                    package_tool_path, 'img_create',
                    '--no_progress_bar',
                    '--tmp_path', temporary_directory,
                    layout_file, destination_file
                ]

                package_success = nimp.sys.process.call(package_command)
                if package_success != 0:
                    raise RuntimeError('Package failed')
