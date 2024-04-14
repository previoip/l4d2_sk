import sys
import traceback
import json
import os.path
import configparser
from requests import Session
from itertools import chain
from collections import namedtuple
from src.log import init_logger
from src.path_utils import ensure_dir, mkdtemp, archive_extract_tar, archive_extract_zip, copy2, delete_file, isfile
from src.http_utils import download_file, http_request

logger = init_logger('setup', 'setup.log')
pwd = os.path.dirname(__file__)

class SetupConfig:
  config_file = 'setup_config.ini'
  platform = 'windows'
  steamapp_info_path = 'steamapp_info.json'
  steamapp_parent_dir = '/steamcmd'
  steamapp_app_dir = '/steamcmd/l4d2'

  @classmethod
  def get_app_path(cls, *tails):
    return os.path.join(cls.steamapp_app_dir, *tails)

  @classmethod
  def load_config(cls):
    parser = configparser.ConfigParser()
    if isfile(cls.config_file):
      logger.info('loading config file: {}'.format(cls.config_file))
      with open(cls.config_file) as fh:
        parser.read_file(fh)
    if not 'setup' in parser:
      parser['setup'] = {}
    if not 'platform' in parser['setup']:
      parser['setup']['platform'] = cls.platform
    if not 'steamapp' in parser:
      parser['steamapp'] = {}
    if not 'info-json' in parser['steamapp']:
      parser['steamapp']['info-json'] = cls.steamapp_info_path
    if not 'parent-dir' in parser['steamapp']:
      parser['steamapp']['parent-dir'] = cls.steamapp_parent_dir
    if not 'app-dir' in parser['steamapp']:
      parser['steamapp']['app-dir'] = cls.steamapp_parent_dir

    cls.platform = parser['setup']['platform']
    cls.steamapp_info_path = parser['steamapp']['info-json']
    cls.steamapp_parent_dir = parser['steamapp']['parent-dir']
    cls.steamapp_app_dir = parser['steamapp']['app-dir']

  @classmethod
  def save_config(cls):
    logger.info('saving config file: {}'.format(cls.config_file))
    parser = configparser.ConfigParser()
    parser['setup'] = {}
    parser['setup']['platform'] = cls.platform
    parser['steamapp'] = {}
    parser['steamapp']['info-json'] = cls.steamapp_info_path
    parser['steamapp']['parent-dir'] = cls.steamapp_parent_dir
    parser['steamapp']['app-dir'] = cls.steamapp_app_dir


class Main:
  downloads_dir = './downloads'
  plugin_t = namedtuple('PluginEnt', ['name', 'resources', 'disable_cache'])
  resource_t = namedtuple('ResourceEnt', ['url', 'extract_path', 'disable_cache'])
  workshop_t = namedtuple('WorkshopEnt', ['workshop_id', 'name', 'rel'])
  setup_config = SetupConfig
  
  def __init__(self):
    self.atexit_callback = list()
    self.session = Session()
    self.atexit_callback.append(self.session.close)
    self.tempdir, d_tempdir = mkdtemp()
    self.atexit_callback.append(d_tempdir)
    self.steamapp_info = dict()
    self.setup_config.load_config()
    self.atexit_callback.append(self.setup_config.save_config)

  def load_steamapp_info(self, json_path):
    if isfile(json_path):
      with open(json_path, 'r') as fh:
        self.steamapp_info.update(json.load(fh))
        return True
    logger.error('cannot load json: {}'.format(json_path))
    return False

  @classmethod
  def _iter_plugins(cls, d):
    meta_plugins = d.get('metaPlugins', list())
    plugins = d.get('plugins', list())
    for plugin in chain(meta_plugins, plugins):
      plugin_name = plugin.get('name')
      if plugin_name is None or not plugin_name:
        continue
      plugin_exclude = plugin.get('exclude', False)
      if plugin_exclude:
        continue
      plugin_extract_path = plugin.get('extractPath', '')
      plugin_resources = plugin.get('resources', [])
      plugin_disable_cache = plugin.get('disableCache', False)
      yield cls.plugin_t(plugin_name, cls._iter_resources(plugin_resources), plugin_disable_cache)

  @classmethod
  def _iter_resources(cls, l):
    for resource in l:
      resource_exclude = resource.get('exclude', False)
      if resource_exclude:
        continue
      resource_platform = resource.get('platform', cls.setup_config.platform)
      if not (resource_platform == cls.setup_config.platform or resource_platform == '*'):
        continue 
      resource_disable_cache = resource.get('disableCache', False)
      resource_extract_path = resource.get('extractPath', '')
      resource_url = resource.get('url')
      yield cls.resource_t(resource_url, resource_extract_path, resource_disable_cache)

  def iter_plugins(self):
    yield from self._iter_plugins(self.steamapp_info)

  @classmethod
  def _iter_workshops(cls, l):
    for workshop_ent in l:
      include, workshop_id, workshop_name, workshop_rel = workshop_ent
      if not include:
        continue
      yield cls.workshop_t(workshop_id, workshop_name, workshop_rel)

  def iter_workshops(self):
    yield from self._iter_workshops(self.steamapp_info.get('workshopIds', []))

  def print_config_stats(self):
    print()
    print('using platform       : {}'.format(self.setup_config.platform))
    print('target app directory : {}'.format(self.setup_config.steamapp_app_dir))

  def print_plugins_stats(self):
    print()
    print('plugins found from steamapp_info:')
    for m, plugin_ent in enumerate(self.iter_plugins()):
      print('{:>3d}. {}'.format(m+1, plugin_ent.name))

  def print_addons_stats(self):
    print()
    print('workshops found from steamapp_info:')
    for m, workshop_ent in enumerate(self.iter_workshops()):
      print('{:>3d}. {}'.format(m+1, workshop_ent.name))


  @staticmethod
  def prompt_confirm():
    print()
    res = input('continue? [Y/n] : ') == 'Y'
    print()
    return res

  def run(self):
    self.load_steamapp_info(self.setup_config.steamapp_info_path)
    ensure_dir(self.downloads_dir)
    if len(sys.argv) > 1 and sys.argv[1] == 'install-workshop':
      self.run_install_workshop()
      return
    self.run_install_all()

  def install_plugin(self, plugin_ent):
    logger.info('installing: {}'.format(plugin_ent.name))
    for n, resource_ent in enumerate(plugin_ent.resources):
      target_dir = self.setup_config.get_app_path(resource_ent.extract_path)
      resource_url = resource_ent.url
      if resource_url is None or resource_url == '':
        logger.warning('url is empty')
        continue
      status, download_path, info = download_file(self.session, resource_url, self.downloads_dir)
      if not status:
        logger.warning('failed to retrieve content: {} {}'.format(plugin_ent.name, resource_url))
        continue
      if info.content_type == 'application/zip' or info.file_type == 'zip':
        logger.info('extracting zip: {}'.format(info.file_name))
        archive_extract_zip(download_path, target_dir, self.tempdir)
      elif info.content_type == 'application/x-xz' or info.file_type.startswith('tar'):
        logger.info('extracting {}: {}'.format(info.file_type, info.file_name))
        archive_extract_tar(download_path, target_dir, self.tempdir)
      else:
        logger.info('copying file: {}'.format(info.file_name))
        copy2(download_path, os.path.join(target_dir, info.file_name))
      if plugin_ent.disable_cache:
        delete_file(download_path)
    logger.info('finished installing plugin: {}'.format(plugin_ent.name))

  def download_workshop(self, workshop_id):
    logger.info('retrieving workshop info: {}'.format(workshop_id))
    db_hostname = 'https://db.steamworkshopdownloader.io'
    db_api_path = 'prod/api/details/file'
    data = bytes(f'[{workshop_id}]', 'utf8')
    db_resp = http_request(self.session, 'POST', url='{}/{}'.format(db_hostname, db_api_path), data=data)
    if db_resp is None:
      logger.warning('cannot retrieve workshop info: {}'.format(workshop_id))
      return
    workshop_db_res = db_resp.json()

    for workshop_ent in workshop_db_res:
    
      result = workshop_ent.get('result')
      file_url = workshop_ent.get('file_url')
      preview_url = workshop_ent.get('preview_url')
      file_name = workshop_ent.get('filename')
      is_collection = workshop_ent.get('show_subscribe_all', False)
      is_collection = is_collection and not workshop_ent.get('can_subscribe', False)

      logger.info('downloading workshop: {}'.format(file_name))

      if result: 
        if is_collection:
          logger.info('workshop collection found instead, processing children')
          for workshop_child_ent in workshop_ent.get('children'):
            workshop_child_id = workshop_child_ent.get('publishedfileid')
            if not workshop_child_id is None:
              self.download_workshop(workshop_child_id)
        else:
          workshop_resource_urls = [file_url, preview_url]
          for workshop_resource_url in workshop_resource_urls:
            if not workshop_resource_url is None:
              export_dir = self.setup_config.get_app_path('left4dead2/addons')
              ensure_dir(export_dir)
              status, download_file_path, file_info = download_file(self.session, workshop_resource_url, export_dir)
              if not status and not download_file_path is None:
                logger.warning('destroying unfinished addon file')
                delete_file(download_file_path)


  def run_install_all(self):
    logger.info('running install all routine')
    self.print_config_stats()
    self.print_plugins_stats()
    self.print_addons_stats()
    if not self.prompt_confirm():
      return
    logger.info('installing plugins')
    for m, plugin_ent in enumerate(self.iter_plugins()):
      self.install_plugin(plugin_ent)
    logger.info('finished installing all plugins')
    logger.info('installing workshops')
    for m, workshop_ent in enumerate(self.iter_workshops()):
      self.download_workshop(workshop_ent.workshop_id)
    logger.info('finished installing all workshops')
    return

  def run_install_workshop(self):
    logger.info('running install workshop routine')
    if not len(sys.argv) > 3:
      logger.warning('no workshop ids')
    workshop_ids = sys.argv[2:]
    self.print_config_stats()
    for m, workshop_id in enumerate(workshop_ids):
      print('{:>3d}. {}'.format(m+1, workshop_id))
      if not workshop_id.isnumeric():
        logger.warning('invalid workshop id: {}'.format(workshop_id))
    if not self.prompt_confirm():
      return
    for workshop_id in workshop_ids:
      if not workshop_id.isnumeric():
        continue
      self.download_workshop(int(workshop_id))
    return

  def exit(self):
    for cb in self.atexit_callback: cb()

if __name__ == '__main__':
  main = Main()
  errno = 0
  try:
    main.session.headers.update({
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36',
    })
    main.run()
  except KeyboardInterrupt as e:
    logger.warning('KeyboardInterrupt')
    print(e)
  except Exception as e:
    print(e)
    logger.error(traceback.format_exc())
    errno = 1
  try:
    main.exit()
    logger.info('program exits{}'.format(' gracefully' if errno == 0  else ''))
  except Exception:
    logger.error(traceback.format_exc())

