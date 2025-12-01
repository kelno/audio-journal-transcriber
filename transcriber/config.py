from pathlib import Path
import os
from pydantic import BaseModel, model_validator

import yaml

CONFIG_FILENAME = "config.yaml"


# Configuration class matching yaml config
class TranscribeConfig(BaseModel):
    class GeneralConfig(BaseModel):
        delete_source_audio_after_days: int = 0  # 0 means disabled
        min_length_seconds: float = 5.0  # 0 means disabled
        remove_short_files: bool = True

    class TextConfig(BaseModel):
        summary_enabled: bool
        api_base_url: str
        model: str
        api_key: str
        extra_context: str | None = None

        @model_validator(mode="after")
        def ensure_trailing_slash(cls, self):  # pylint: disable=E0213
            if not self.api_base_url.endswith("/"):
                self.api_base_url += "/"
            return self

    class AudioConfig(BaseModel):
        api_base_url: str
        model: str
        api_key: str
        stream: bool = False

        @model_validator(mode="after")
        def ensure_trailing_slash(cls, self):  # pylint: disable=E0213
            if not self.api_base_url.endswith("/"):
                self.api_base_url += "/"
            return self

    general: GeneralConfig
    text: TextConfig
    audio: AudioConfig

    def get_min_audio_length_seconds(self) -> float | None:
        """Return the minimal audio length in seconds to process, or None if disabled."""

        if self.general.min_length_seconds <= 0.0:
            return None
        else:
            return self.general.min_length_seconds

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "TranscribeConfig":
        yaml_path = os.path.join(config_dir, CONFIG_FILENAME)
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        return TranscribeConfig(**raw)
