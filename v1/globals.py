from pathlib import Path
from platformdirs import user_config_dir

APP_NAME = 't0d0'
CONFIG_DIR = Path(user_config_dir(APP_NAME))
CONFIG_FILE = CONFIG_DIR / 'config.json'
