import os
import subprocess
from src.log import init_logger
from src.pathlib import isfile, ensure_dir, archive_extract_zip
from src.http_utils import http_request, download_file

logger = init_logger('steamapp_manager', 'setup.log')

class SteamAppManager:

  steamcmd_file_host = 'https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip'
  steamcmd_dir = '/steamcmd'
  steamcmd_file_name = 'steamcmd.exe'

  def __init__(self, appid='', ds_appid='', ds_install_dir='', ds_file_path=''):
    self.appid = appid
    self.ds_appid = ds_appid
    self.ds_install_dir = ds_install_dir
    self.ds_file_path = ds_file_path
    self._userid = ''
    self._passwd = ''
    self.configure_auth('', '')

  def configure(self, appid, ds_appid, ds_install_dir, ds_file_path):
    self.appid = appid
    self.ds_appid = ds_appid
    self.ds_install_dir = ds_install_dir
    self.ds_file_path = ds_file_path

  def configure_auth(self, userid, passwd):
    self._userid = userid if userid else 'anonymous'
    self._passwd = passwd

  @classmethod
  def get_steamcmd_path(cls, *tails):
    return os.path.join(cls.steamcmd_dir, cls.steamcmd_file_name, *tails)

  @classmethod
  def is_steamcmd_installed(cls):
    return isfile(cls.get_steamcmd_path())

  def get_app_path(self, *tails):
    return os.path.join(self.steamcmd_dir, self.ds_install_dir, *tails)

  @classmethod
  def download_steamcmd(cls, session):
    if not cls.is_steamcmd_installed():
      logger.info('downloading steamcmd')
      status, archive_path = download_file(session, cls.steamcmd_file_host, './downloads', 'steamcmd.zip')
      if status:
        archive_extract_zip(archive_path, cls.steamcmd_dir)
      logger.info('extracted: {}'.format(cls.get_steamcmd_path()))
    else:
      logger.info('stamcmd already exists: {}'.format(cls.get_steamcmd_path()))

  def prepare_args_update_app(self, validate=False):
    args = [
      self.get_steamcmd_path(),
      '+force_install_dir', self.ds_install_dir,
      '+login', 'anonymous',
      '+app_update', self.ds_appid
    ]
    if validate:
      args += ['+validate']
    return args + ['+quit']

  def prepare_args_workshop_download_item(self, workshop_item_id):
    args = [
      self.get_steamcmd_path(),
      '+force_install_dir', self.ds_install_dir,
      '+login', str(self._userid), str(self._passwd),
      '+workshop_download_item', self.appid, str(workshop_item_id)
    ]
    return args + ['+quit']

  def update_app(self, validate=False):
    return subprocess.call(self.prepare_args_update_app(validate=validate))

  def workshop_download_item(self, workshop_item_id):
    retcode = subprocess.call(self.prepare_args_workshop_download_item(workshop_item_id=workshop_item_id))
    return retcode

  def workshop_download_item_extern(self, session, workshop_item_id):
    logger.info('retrieving workshop info: {}'.format(workshop_item_id))
    home = 'https://steamworkshopdownloader.io/'
    db_hostname = 'https://db.steamworkshopdownloader.io'
    db_api_path = 'prod/api/details/file'
    data = bytes(f'[{workshop_item_id}]', 'utf8')

    db_resp = http_request(session, 'POST', url='{}/{}'.format(db_hostname, db_api_path), data=data)
    if db_resp is None:
      logger.warning('cannot retrieve workshop info: {}'.format(workshop_item_id))

    # workshop_db_res = json.loads(db_resp.content)
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
              self.workshop_download_item_extern(session, workshop_child_id)
        else:
          if not file_url is None:
            export_dir = self.get_app_path('left4dead2/addons')
            ensure_dir(export_dir)
            download_file(session, file_url, export_dir)

          if not preview_url is None:
            export_dir = self.get_app_path('left4dead2/addons')
            ensure_dir(export_dir)
            download_file(session, preview_url, export_dir)
      else:
        logger.warning('missing workshop info: {}'.format(workshop_item_id))