"""Log in to course sites locally with a real browser, then upload the captured
session state to your StudyLens account on a hosted (e.g. Railway) deployment."""

from studylens.tools.browser_state import push_user_browser_state

if __name__ == "__main__":
    push_user_browser_state()
