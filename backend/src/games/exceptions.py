from src.exceptions import AppError


class GameNotFound(AppError):
    status_code = 404

    def __init__(self, appid: int):
        super().__init__(f"Steam has no app with appid {appid}")


class RetryNotAllowed(AppError):
    status_code = 403

    def __init__(self):
        super().__init__(
            "This deployment doesn't analyze games on request, so a failed one can't be retried here."
        )


class NotFailed(AppError):
    status_code = 409

    def __init__(self, appid: int):
        super().__init__(f"App {appid} has no failed analysis to retry")
