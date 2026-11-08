from config.exceptions import APIError


class VersionConflictError(APIError):
    status_code = 409
    default_detail = "Resource was modified. Refresh and retry."
    default_code = "version_conflict"
    error_code = "VERSION_CONFLICT"


class UnsupportedFileTypeError(APIError):
    status_code = 400
    default_detail = "Unsupported file type. Allowed: PDF, DOCX, TXT."
    default_code = "unsupported_file_type"
    error_code = "UNSUPPORTED_FILE_TYPE"


class FileTooLargeError(APIError):
    status_code = 400
    default_detail = "File is too large."
    default_code = "file_too_large"
    error_code = "FILE_TOO_LARGE"


class ResourceFileNotFoundError(APIError):
    status_code = 404
    default_detail = "This resource has no uploaded file."
    default_code = "file_not_found"
    error_code = "FILE_NOT_FOUND"


class FileNotUploadedError(APIError):
    status_code = 409
    default_detail = "The file has not been uploaded to storage yet. Upload it and try again."
    default_code = "file_not_uploaded"
    error_code = "FILE_NOT_UPLOADED"


class StorageUnavailableError(APIError):
    status_code = 502
    default_detail = "File storage is temporarily unavailable. Try again later."
    default_code = "storage_error"
    error_code = "STORAGE_ERROR"
