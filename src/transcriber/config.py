from pathlib import Path
from pydantic import BaseModel, model_validator

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class GeneralConfig(BaseModel):
    input_dir: Path
    store_dir: Path
    delete_source_audio_after_days: int  # 0 means disabled
    min_length_seconds: float  # 0 means disabled
    remove_short_files: bool

    def __str__(self):
        return f"GeneralConfig(input_dir={self.input_dir}, store_dir={self.store_dir}, delete_source_audio_after_days={self.delete_source_audio_after_days if self.delete_source_audio_after_days else "disabled"}, min_length_seconds={self.min_length_seconds if self.min_length_seconds else "disabled"}, remove_short_files={self.remove_short_files})"


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

    def __str__(self):
        return f"TextConfig(summary_enabled={self.summary_enabled}, api_base_url={self.api_base_url}, model={self.model}, api_key={'***' if self.api_key else 'None'}, extra_context={self.extra_context})"


class AudioConfig(BaseModel):
    api_base_url: str
    model: str
    api_key: str
    stream: bool

    @model_validator(mode="after")
    def ensure_trailing_slash(cls, self):  # pylint: disable=E0213
        if not self.api_base_url.endswith("/"):
            self.api_base_url += "/"
        return self

    def __str__(self):
        return f"AudioConfig(api_base_url={self.api_base_url}, model={self.model}, api_key={'***' if self.api_key else 'None'}, stream={self.stream})"


default_toml_file = Path(__file__).parent / "config.default.toml"
assert default_toml_file.exists()


class TranscribeConfig(BaseSettings):
    """All settings for the transcriber. Values come from `config.default.toml` and `config.custom.toml` files and are
    automatically overridden by environment variables prefixed with `TRANSCRIBER_` using `__` as a nested delimiter.

    Priority (highest to lowest):
    1. Environment variables (TRANSCRIBER_*)
    2. toml files in config directory
    3. Default values in model
    """

    model_config = SettingsConfigDict(
        env_prefix="TRANSCRIBER_",
        env_nested_delimiter="__",
        extra="forbid",
        toml_file=[
            default_toml_file,
            "config.custom.toml",  # from current working directory
        ],
    )

    general: GeneralConfig
    text: TextConfig
    audio: AudioConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # default order is init_settings, env_settings, dotenv_settings, file_secret_settings
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    def get_min_audio_length_seconds(self) -> float | None:
        """Return the minimal audio length in seconds to process, or None if disabled."""

        if self.general.min_length_seconds <= 0.0:
            return None
        else:
            return self.general.min_length_seconds


global_config: TranscribeConfig | None = None


def get_config() -> TranscribeConfig:
    global global_config  # pylint: disable=global-statement

    if global_config is None:
        global_config = TranscribeConfig()  # type: ignore

    return global_config
