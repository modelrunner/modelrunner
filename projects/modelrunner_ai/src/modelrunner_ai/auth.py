import os
from threading import Lock
from typing import Optional


class GoogleColabState:
    def __init__(self):
        self.is_checked = False
        self.lock = Lock()
        self.secret: Optional[str] = None


_colab_state = GoogleColabState()


def is_google_colab() -> bool:
    try:
        from IPython import get_ipython

        return "google.colab" in str(get_ipython())
    except ModuleNotFoundError:
        return False
    except NameError:
        return False


def get_colab_token() -> Optional[str]:
    if not is_google_colab():
        return None
    with _colab_state.lock:
        if _colab_state.is_checked:  # request access only once
            return _colab_state.secret

        try:
            from google.colab import userdata  # noqa: I001
        except ImportError:
            return None

        try:
            token = userdata.get("MODELRUNNER_KEY")
            _colab_state.secret = token.strip()
        except Exception:
            _colab_state.secret = None

        _colab_state.is_checked = True
        return _colab_state.secret


class MissingCredentialsError(Exception):
    pass


MODELRUNNER_RUN_HOST = os.environ.get("MODELRUNNER_RUN_HOST", "modelrunner.run")


def fetch_credentials() -> str:
    if key := os.getenv("MODELRUNNER_KEY"):
        return key
    elif (key_id := os.getenv("MODELRUNNER_KEY_ID")) and (
        modelrunner_key_secret := os.getenv("MODELRUNNER_KEY_SECRET")
    ):
        return f"{key_id}:{modelrunner_key_secret}"
    elif colab_token := get_colab_token():
        return colab_token
    else:
        raise MissingCredentialsError(
            "Please set the MODELRUNNER_KEY environment variable to your API key, or set the MODELRUNNER_KEY_ID and MODELRUNNER_KEY_SECRET environment variables."
        )
