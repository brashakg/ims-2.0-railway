# ============================================================================
# IMS 2.0 - Two-Factor Authentication (2FA) Implementation
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
import secrets
import qrcode
from io import BytesIO
import base64
import pyotp
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

# ============================================================================
# Models
# ============================================================================

class Enable2FARequest(BaseModel):
    password: str

class Enable2FAResponse(BaseModel):
    qr_code: str
    secret_key: str
    backup_codes: list[str]

class Verify2FARequest(BaseModel):
    code: str

class Disable2FARequest(BaseModel):
    password: str
    code: str

class BackupCodeRequest(BaseModel):
    code: str

# ============================================================================
# Two-Factor Authentication Service
# ============================================================================

class TwoFactorAuthService:
    """Service for managing 2FA operations"""

    @staticmethod
    def generate_secret() -> str:
        """Generate a new TOTP secret"""
        return pyotp.random_base32()

    @staticmethod
    def generate_qr_code(secret: str, email: str, issuer: str = "IMS 2.0") -> str:
        """Generate QR code for authenticator app setup"""
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(
            name=email,
            issuer_name=issuer
        )

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(uri)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()

        return f"data:image/png;base64,{img_str}"

    @staticmethod
    def generate_backup_codes(count: int = 10) -> list[str]:
        """Generate backup codes for account recovery"""
        return [secrets.token_hex(4) for _ in range(count)]

    @staticmethod
    def verify_totp(secret: str, code: str, window: int = 1) -> bool:
        """Verify TOTP code with time window tolerance"""
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=window)

    @staticmethod
    def verify_backup_code(code: str, stored_codes: list[str]) -> bool:
        """Verify backup code and return True if valid"""
        return code in stored_codes

# ============================================================================
# Endpoints
# ============================================================================

@router.post("/2fa/enable", response_model=Enable2FAResponse)
async def enable_2fa(
    request: Enable2FARequest,
    current_user = Depends(get_current_user)
):
    """Enable 2FA for authenticated user"""

    # Verify password
    user = await db.get_user(current_user.id)
    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password"
        )

    # Generate secret and QR code
    secret = TwoFactorAuthService.generate_secret()
    qr_code = TwoFactorAuthService.generate_qr_code(secret, user.email)
    backup_codes = TwoFactorAuthService.generate_backup_codes()

    # Store temporarily in session (not confirmed yet)
    # User must verify with code before activation

    return Enable2FAResponse(
        qr_code=qr_code,
        secret_key=secret,
        backup_codes=backup_codes
    )

@router.post("/2fa/verify")
async def verify_2fa(
    request: Verify2FARequest,
    current_user = Depends(get_current_user)
):
    """Verify 2FA setup and activate"""

    # Get temporary 2FA data from session
    temp_2fa = session.get("temp_2fa_data")
    if not temp_2fa:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA setup not initiated"
        )

    secret = temp_2fa["secret"]
    backup_codes = temp_2fa["backup_codes"]

    # Verify the code
    if not TwoFactorAuthService.verify_totp(secret, request.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code"
        )

    # Save to database
    await db.update_user(
        current_user.id,
        {
            "totp_secret": secret,
            "backup_codes": backup_codes,
            "is_2fa_enabled": True,
            "two_fa_enabled_at": datetime.utcnow()
        }
    )

    # Audit log
    await audit_log.log_event(
        user_id=current_user.id,
        action="2fa_enabled",
        resource="auth",
        details={"method": "totp"},
        ip_address=request.client.host
    )

    # Clear session
    del session["temp_2fa_data"]

    return {"message": "2FA enabled successfully"}

@router.post("/2fa/verify-login")
async def verify_2fa_login(
    request: Verify2FARequest,
    user_id: str  # From incomplete login
):
    """Verify 2FA code during login"""

    user = await db.get_user(user_id)
    if not user.is_2fa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled"
        )

    # Try TOTP first
    if TwoFactorAuthService.verify_totp(user.totp_secret, request.code):
        # Valid TOTP code
        return create_login_response(user)

    # Try backup code
    if TwoFactorAuthService.verify_backup_code(request.code, user.backup_codes):
        # Valid backup code - remove it
        user.backup_codes.remove(request.code)
        await db.update_user(user_id, {"backup_codes": user.backup_codes})

        # Audit log
        await audit_log.log_event(
            user_id=user_id,
            action="2fa_backup_code_used",
            resource="auth",
            details={"remaining_codes": len(user.backup_codes)}
        )

        return create_login_response(user)

    # Invalid code
    await audit_log.log_event(
        user_id=user_id,
        action="2fa_verification_failed",
        resource="auth",
        severity="warning"
    )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid verification code"
    )

@router.post("/2fa/disable")
async def disable_2fa(
    request: Disable2FARequest,
    current_user = Depends(get_current_user)
):
    """Disable 2FA"""

    user = await db.get_user(current_user.id)

    # Verify password
    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password"
        )

    # Verify current 2FA code
    if not TwoFactorAuthService.verify_totp(user.totp_secret, request.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code"
        )

    # Disable 2FA
    await db.update_user(
        current_user.id,
        {
            "is_2fa_enabled": False,
            "totp_secret": None,
            "backup_codes": []
        }
    )

    # Audit log
    await audit_log.log_event(
        user_id=current_user.id,
        action="2fa_disabled",
        resource="auth",
        severity="high"
    )

    return {"message": "2FA disabled successfully"}

@router.post("/2fa/backup-codes/regenerate")
async def regenerate_backup_codes(
    request: Disable2FARequest,
    current_user = Depends(get_current_user)
):
    """Regenerate backup codes"""

    user = await db.get_user(current_user.id)

    if not user.is_2fa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA not enabled"
        )

    # Verify current 2FA code
    if not TwoFactorAuthService.verify_totp(user.totp_secret, request.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code"
        )

    # Generate new codes
    new_codes = TwoFactorAuthService.generate_backup_codes()

    # Update database
    await db.update_user(
        current_user.id,
        {"backup_codes": new_codes}
    )

    # Audit log
    await audit_log.log_event(
        user_id=current_user.id,
        action="backup_codes_regenerated",
        resource="auth"
    )

    return {"backup_codes": new_codes}

@router.get("/2fa/status")
async def get_2fa_status(
    current_user = Depends(get_current_user)
):
    """Get 2FA status for current user"""

    user = await db.get_user(current_user.id)

    return {
        "is_enabled": user.is_2fa_enabled,
        "enabled_at": user.two_fa_enabled_at,
        "backup_codes_remaining": len(user.backup_codes) if user.backup_codes else 0
    }
