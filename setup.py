import traceback
import json
from requests import Session
from itertools import chain
from src.log import init_logger
from src.steamcmd_manager import SteamAppManager
from src.pathlib import ensure_dir, mkdtemp, archive_extract_tar, archive_extract_zip, copy2, delete_file
from src.http_utils import download_file

steamapp_info_path = './steamapp_info.json'
override_server_cfg = False

logger = init_logger('setup')

class Main:
  temp_download_dir = './downloads'
  template_server_cfg_path = './server.cfg'

  def __init__(self, session: Session):
    self.deferred_callbacks = list()
    self.session = session
    self.deferred_callbacks.append(self.session.close)
    self.tmpdir, d_tmpdir = mkdtemp()
    self.deferred_callbacks.append(d_tmpdir)
    ensure_dir(self.temp_download_dir)

  def exit(self):
    for cb in self.deferred_callbacks: cb()

  def run(self):
    with open(steamapp_info_path, 'r') as fh:
      steamapp_info = dict(json.load(fh))

    man = SteamAppManager()
    man.configure_steamcmd_path('/stemacmd')

    appid = steamapp_info['config'].get('appid', '550')
    appid_dedicated_server = steamapp_info['config'].get('appidDedicatedServer', '222860')
    install_force_dir = steamapp_info['config'].get('installForceDir', 'l4d2')
    file_path_dedicated_server = steamapp_info['config'].get('filePathDedicatedServer', 'srcds.exe')
    use_platform = steamapp_info['config'].get('usePlatform', 'linux')

    meta_plugins = steamapp_info.get('metaPlugins', list())
    plugins = steamapp_info.get('plugins', list())

    man.configure(
      appid,
      appid_dedicated_server,
      install_force_dir,
      file_path_dedicated_server
    )

    # man.download_steamcmd(session=self.session)
    # man.update_app()

    for plugin in chain(meta_plugins, plugins):
      plugin_name = plugin.get('name')
      logger.info('installing plugin: {}'.format(plugin_name))
      plugin_exclude = plugin.get('exclude', False)
      if plugin_exclude or plugin_name is None or plugin_name == '':
        logger.info('plugin excluded')
        continue
      plugin_extract_path = plugin.get('extractPath')
      plugin_resources = plugin.get('resources', [])
      plugin_disable_cache = plugin.get('disableCache', False)
      for resource in plugin_resources:
        resource_exclude = resource.get('exclude', False)
        if resource_exclude:
          logger.info('resource excluded')
          continue
        resource_extract_path = resource.get('extractPath')
        resource_platform = resource.get('platform', use_platform)
        resource_url = resource.get('url')
        resource_file_name = resource.get('fileName')
        resource_do_compile = resource.get('doCompile', False)
        resource_compiler_location = resource.get('compilerLocation')
        resource_compiler_params = resource.get('compilerParams')

        if not (resource_platform == use_platform or resource_platform == '*'):
          continue
        if resource_url is None or resource_url == '':
          continue
        if resource_extract_path is None:
          if plugin_extract_path is None:
            logger.warning('missing extractPath on plugin: {}'.format(plugin_name))
            continue
          resource_extract_path = plugin_extract_path

        status, download_path, info = download_file(self.session, resource_url, self.temp_download_dir)
        if not status:
          logger.warning('failed to retrieve content: {} {}'.format(plugin_name, resource_url))
          continue

        if info.content_type == 'application/zip' or info.file_type == 'zip':
          logger.info('extracting zip: {}'.format(info.file_name))
          archive_extract_zip(download_path, man.get_app_path(resource_extract_path), self.tmpdir)
        elif info.content_type == 'application/x-xz':
          logger.info('extracting tar.xz: {}'.format(info.file_name))
          archive_extract_tar(download_path, man.get_app_path(resource_extract_path), self.tmpdir)
        else:
          logger.info('copying file: {}'.format(info.file_name))
          copy2(download_path, man.get_app_path(resource_extract_path))
        if plugin_disable_cache:
          delete_file(download_path)

      logger.info('finished installing plugin: {}'.format(plugin_name))
    logger.info('finished installing all plugins')

    server_cfg_path = man.get_app_path('left4dead2', 'cfg', 'server.cfg')
    if override_server_cfg or not file_utils.isfile(server_cfg_path):
      logger.info('copying server config template')
      copy2(self.template_server_cfg_path, server_cfg_path)

    workshop_ids = steamapp_info.get('workshopIds', [])
    if workshop_ids:
      logger.info('downloading workshops')
      for workshop_id, workshop_name, workshop_rel in workshop_ids:
        man.workshop_download_item_extern(self.session, workshop_id)

    return


if __name__ == '__main__':
  errno = 0

  session = Session()
  session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36',
  })
  main = Main(session)
  try:
    main.run()
  except Exception:
    logger.error(traceback.format_exc())
    errno = 1
  except KeyboardInterrupt:
    logger.error(traceback.format_exc())
    pass

  try:
    main.exit()
    logger.info('program exits{}'.format(' gracefully' if errno == 0  else ''))
  except Exception:
    logger.error(traceback.format_exc())

