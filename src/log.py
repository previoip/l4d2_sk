import os
import logging

fmt_default = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fmt_simple = logging.Formatter('{name:<12} [{levelname:<7}]: {message}', style='{')

def init_logger(name: str, file_name=None, level=logging.DEBUG, do_stream_file=True, do_stream_stdout=True):
  logger = logging.getLogger(name)
  logger.setLevel(level)
  logger.handlers.clear()
  if do_stream_stdout:
    logger_h = logging.StreamHandler()
    logger_h.setFormatter(fmt_simple)
    logger.addHandler(logger_h)
  if do_stream_file:
    os.makedirs('./log', exist_ok=True)
    if file_name is None:
      file_name = name + '.log'
    logger_h = logging.FileHandler(os.path.join('./log', file_name))
    logger_h.setFormatter(fmt_default)
    logger.addHandler(logger_h)
  return logger