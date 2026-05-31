from pathlib import Path
from typing import override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class GeneralConfig(BaseModel):
    """Configuration for general settings."""

    input_dir: Path
    store_dir: Path
    delete_source_audio_after_days: int  # 0 means disabled
    min_length_seconds: float  # 0 means disabled
    remove_short_files: bool
    timezone: ZoneInfo
    safe_delete: bool

    @override
    def __str__(self) -> str:
        """Return a string representation of the GeneralConfig."""
        return (
            f"GeneralConfig(input_dir={self.input_dir}, store_dir={self.store_dir}, "
            f"delete_source_audio_after_days={self.delete_source_audio_after_days or 'disabled'}, "
            f"min_length_seconds={self.min_length_seconds or 'disabled'}, "
            f"remove_short_files={self.remove_short_files})"
        )


class TextConfig(BaseModel):
    """Configuration for text-related settings."""

    summary_enabled: bool
    api_base_url: str
    model: str
    api_key: str
    extra_context: str | None = None

    @model_validator(mode="after")
    def ensure_trailing_slash(self) -> "TextConfig":
        """Ensure the API base URL ends with a trailing slash."""
        if self.api_base_url and not self.api_base_url.endswith("/"):
            self.api_base_url += "/"
        return self

    @override
    def __str__(self) -> str:
        """Return a string representation of the TextConfig."""
        return (
            f"TextConfig(summary_enabled={self.summary_enabled}, api_base_url={self.api_base_url}, "
            f"model={self.model}, api_key={'***' if self.api_key else 'None'}, "
            f"extra_context={self.extra_context})"
        )


class AudioConfig(BaseModel):
    """Configuration for audio-related settings."""

    api_base_url: str
    model: str
    api_key: str
    stream: bool

    @model_validator(mode="after")
    def ensure_trailing_slash(self) -> "AudioConfig":
        """Ensure the API base URL ends with a trailing slash."""
        if self.api_base_url and not self.api_base_url.endswith("/"):
            self.api_base_url += "/"
        return self

    @override
    def __str__(self) -> str:
        """Return a string representation of the AudioConfig."""
        return (
            f"AudioConfig(api_base_url={self.api_base_url}, model={self.model}, "
            f"api_key={'***' if self.api_key else 'None'}, stream={self.stream})"
        )


default_toml_file = Path(__file__).parent / "config.default.toml"
if not default_toml_file.exists():
    error_msg = f"Default configuration file not found: {default_toml_file}"
    raise FileNotFoundError(error_msg)


class TranscribeConfig(BaseSettings):
    """All settings for the transcriber.

    Values come from `config.default.toml` and `config.custom.toml` files and are
    automatically overridden by environment variables prefixed with `TRANSCRIBER_`
    using `__` as a nested delimiter.

    Priority (highest to lowest):
    1. Environment variables (TRANSCRIBER_*)
    2. TOML files in config directory
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
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize the sources for settings.

        Default order is init_settings, env_settings, dotenv_settings, file_secret_settings.
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    def get_min_audio_length_seconds(self) -> float | None:
        """Return the minimal audio length in seconds to process, or None if disabled.

        Returns:
            float | None: The minimal audio length in seconds, or None if disabled.

        """
        return self.general.min_length_seconds if self.general.min_length_seconds > 0.0 else None
