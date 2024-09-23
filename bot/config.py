import yaml
import dotenv
from pathlib import Path
import os

config_dir = Path(__file__).parent.parent.resolve() / "config"

# load yaml config
with open(config_dir / "config.yml", 'r') as f:
    config_yaml = yaml.safe_load(f)

# load .env config
config_env = dotenv.dotenv_values(config_dir / "config.env")
# config_env = os.environ
# config parameters
telegram_token = config_yaml["telegram_token"]
telegram_api_id = config_yaml["telegram_api_id"]
telegram_api_hash = config_yaml["telegram_api_hash"]
openai_api_key = config_yaml["openai_api_key"]
openai_api_assistant = config_yaml["openai_api_assistant"]
openai_api_base = config_yaml.get("openai_api_base", None)
openai_api_organization= config_yaml.get("openai_api_organization", None)
openai_api_project = config_yaml.get("openai_api_project", None)
allowed_telegram_usernames = config_yaml["allowed_telegram_usernames"]
new_dialog_timeout = config_yaml["new_dialog_timeout"]
enable_message_streaming = config_yaml.get("enable_message_streaming", True)
return_n_generated_images = config_yaml.get("return_n_generated_images", 1)
image_size = config_yaml.get("image_size", "512x512")
n_chat_modes_per_page = config_yaml.get("n_chat_modes_per_page", 5)
mongo_host=config_env.get("MONGO_HOST", "localhost")
mongodb_uri = f"mongodb://{mongo_host}:{config_env['MONGODB_PORT']}"

# chat_modes
with open(config_dir / "chat_modes.yml", 'r') as f:
    chat_modes = yaml.safe_load(f)

# models
with open(config_dir / "models.yml", 'r') as f:
    models = yaml.safe_load(f)

# files
help_group_chat_video_path = Path(__file__).parent.parent.resolve() / "static" / "help_group_chat.mp4"
