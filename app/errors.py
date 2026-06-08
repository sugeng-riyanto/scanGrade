class ScanGradeException(Exception):
    status_code = 400

    def __init__(self, message, error_code=None, user_message=None, status_code=None, details=None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.user_message = user_message or message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


class FileTooLargeError(ScanGradeException):
    status_code = 413

    def __init__(self, file_size, max_size):
        msg = f"File {file_size/1024/1024:.1f}MB melebihi batas {max_size/1024/1024:.0f}MB"
        super().__init__(
            msg, error_code="FILE_TOO_LARGE",
            user_message=f"File terlalu besar. Maksimal: {max_size/1024/1024:.0f}MB",
            details={"file_size": file_size, "max_size": max_size},
        )


class InvalidPDFError(ScanGradeException):
    status_code = 422

    def __init__(self, reason):
        super().__init__(
            f"Invalid PDF: {reason}", error_code="INVALID_PDF",
            user_message="PDF tidak valid. Coba gunakan PDF lain.",
            details={"reason": reason},
        )


class AIProcessingError(ScanGradeException):
    status_code = 422

    def __init__(self, provider, reason):
        super().__init__(
            f"AI {provider} processing failed: {reason}", error_code="AI_PROCESSING_ERROR",
            user_message="Gagal memproses dengan AI. Coba beberapa saat lagi.",
            details={"provider": provider, "reason": reason},
        )


class GradingError(ScanGradeException):
    status_code = 500

    def __init__(self, submission_id, reason):
        super().__init__(
            f"Grading failed for submission {submission_id}: {reason}",
            error_code="GRADING_ERROR",
            user_message="Terjadi kesalahan saat mengoreksi. Tim support sedang menangani.",
            details={"submission_id": submission_id, "reason": reason},
        )


class NotFoundError(ScanGradeException):
    status_code = 404

    def __init__(self, entity_type, entity_id):
        super().__init__(
            f"{entity_type} not found: {entity_id}", error_code="NOT_FOUND",
            user_message=f"{entity_type} tidak ditemukan.",
            details={"entity_type": entity_type, "entity_id": entity_id},
        )


class ForbiddenError(ScanGradeException):
    status_code = 403

    def __init__(self, reason="Akses ditolak"):
        super().__init__(
            f"Forbidden: {reason}", error_code="FORBIDDEN",
            user_message=reason,
        )


class ValidationError(ScanGradeException):
    status_code = 422

    def __init__(self, field, reason):
        super().__init__(
            f"Validation failed for {field}: {reason}", error_code="VALIDATION_ERROR",
            user_message=f"{field}: {reason}",
            details={"field": field, "reason": reason},
        )


class PaymentError(ScanGradeException):
    status_code = 400

    def __init__(self, reason, order_id=None):
        super().__init__(
            f"Payment error: {reason}", error_code="PAYMENT_ERROR",
            user_message=reason,
            details={"order_id": order_id},
        )


class SubscriptionError(ScanGradeException):
    status_code = 403

    def __init__(self, reason="Status langganan bermasalah"):
        super().__init__(
            f"Subscription error: {reason}", error_code="SUBSCRIPTION_ERROR",
            user_message=reason,
        )
