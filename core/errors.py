class AutoChartError(Exception):
    pass

class InputError(AutoChartError):
    pass

class DownloadError(AutoChartError):
    pass

class FFmpegMissing(AutoChartError):
    pass

class AnalysisError(AutoChartError):
    pass

class WriteError(AutoChartError):
    pass

class SpotifyAuthError(AutoChartError):
    pass
