from agent.wall_detect import Wall, detect_wall


def test_cloudflare_text_phrase():
    text = (
        "dictionary.cambridge.org | Performing security verification |  | "
        "This website uses a security service to protect against malicious bots."
    )
    assert detect_wall(text=text, url="https://dictionary.cambridge.org/") is Wall.CLOUDFLARE


def test_cloudflare_checking_browser_phrase():
    text = "Checking your browser before accessing example.com"
    assert detect_wall(text=text, url="https://example.com/") is Wall.CLOUDFLARE


def test_cloudflare_url_token():
    text = "irrelevant body text"
    url = (
        "https://dictionary.cambridge.org/dictionary/english/ephemeral?"
        "__cf_chl_rt_tk=abc123-1.0.1.1-xyz"
    )
    assert detect_wall(text=text, url=url) is Wall.CLOUDFLARE


def test_captcha_recaptcha():
    text = "Please complete the reCAPTCHA below to continue."
    assert detect_wall(text=text, url="https://example.com/") is Wall.CAPTCHA


def test_captcha_hcaptcha():
    text = "hCaptcha challenge: verify you are human."
    assert detect_wall(text=text, url="https://example.com/") is Wall.CAPTCHA


def test_captcha_im_not_a_robot():
    text = "I'm not a robot — please tick the box."
    assert detect_wall(text=text, url="https://example.com/") is Wall.CAPTCHA


def test_login_wall_text():
    text = "Sign in to continue. You must be logged in to view this content."
    assert detect_wall(text=text, url="https://example.com/article/42") is Wall.LOGIN


def test_login_wall_url_path():
    text = "username password forgot"
    assert detect_wall(text=text, url="https://example.com/login?redirect=/foo") is Wall.LOGIN


def test_login_wall_signin_path():
    text = "username password forgot"
    assert detect_wall(text=text, url="https://example.com/signin") is Wall.LOGIN


def test_clean_page_returns_none():
    text = (
        "Hugging Face | Models | google-bert/bert-base-uncased | Downloads last month "
        "| 59,513,990 | Safetensors | Model size | 0.1B params"
    )
    assert (
        detect_wall(text=text, url="https://huggingface.co/google-bert/bert-base-uncased") is None
    )


def test_empty_inputs_return_none():
    assert detect_wall(text="", url="") is None
    assert detect_wall(text=None, url=None) is None
