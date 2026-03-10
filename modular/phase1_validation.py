"""
Input validation utilities for Horizon Finder.

Provides consistent validation across all user inputs and form submissions.
"""

from typing import Tuple, Optional
from .phase1_config import CFG
import re


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


class Validator:
    """Encapsulates validation logic for common input types."""
    
    @staticmethod
    def email(email: str) -> Tuple[bool, Optional[str]]:
        """
        Validate email address.
        
        Args:
            email: Email string to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        email = email.strip()
        if not email:
            return False, "Email is required."
        if len(email) > CFG.MAX_EMAIL_LENGTH:
            return False, f"Email exceeds {CFG.MAX_EMAIL_LENGTH} characters."
        
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, email):
            return False, "Please enter a valid email address."
        return True, None
    
    @staticmethod
    def password(password: str, confirm_password: str = "") -> Tuple[bool, Optional[str]]:
        """
        Validate password.
        
        Args:
            password: Password to validate
            confirm_password: Confirmation password (if provided, must match)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not password:
            return False, "Password is required."
        if len(password) < CFG.MIN_PASSWORD_LENGTH:
            return False, f"Password must be at least {CFG.MIN_PASSWORD_LENGTH} characters."
        if confirm_password and password != confirm_password:
            return False, "Passwords do not match."
        return True, None
    
    @staticmethod
    def name(name: str) -> Tuple[bool, Optional[str]]:
        """
        Validate full name.
        
        Args:
            name: Name to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        name = name.strip()
        if not name:
            return False, "Name is required."
        if len(name) < 2:
            return False, "Name must be at least 2 characters."
        if len(name) > 100:
            return False, "Name must be less than 100 characters."
        return True, None
    
    @staticmethod
    def organization_profile(profile: str) -> Tuple[bool, Optional[str]]:
        """
        Validate organization profile text.
        
        Args:
            profile: Profile text to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        profile = profile.strip()
        if not profile:
            return False, "Profile text is required."
        if len(profile) < 50:
            return False, "Profile should be at least 50 characters for meaningful recommendations."
        if len(profile) > CFG.MAX_PROFILE_LENGTH:
            return False, f"Profile exceeds {CFG.MAX_PROFILE_LENGTH} characters."
        return True, None
    
    @staticmethod
    def topic_id(topic_id: str) -> Tuple[bool, Optional[str]]:
        """
        Validate Horizon topic ID format.
        
        Args:
            topic_id: Topic ID to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        topic_id = topic_id.strip().upper()
        if not topic_id:
            return False, "Topic ID is required."
        
        # Basic HORIZON format check
        pattern = r"^HORIZON-[A-Z0-9]+-\d{4}-[\w-]+-\d{1,2}(?:-\d{1,2})?$"
        if not re.match(pattern, topic_id):
            return False, f"Invalid topic ID format: {topic_id}"
        return True, None


def validate_signup_form(
    email: str,
    name: str,
    password: str,
    password_confirm: str,
) -> Tuple[bool, Optional[str]]:
    """
    Validate complete signup form.
    
    Args:
        email: Email address
        name: Full name
        password: Password
        password_confirm: Password confirmation
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    is_valid, error = Validator.email(email)
    if not is_valid:
        return False, error
    
    is_valid, error = Validator.name(name)
    if not is_valid:
        return False, error
    
    is_valid, error = Validator.password(password, password_confirm)
    if not is_valid:
        return False, error
    
    return True, None


def validate_login_form(email: str, password: str) -> Tuple[bool, Optional[str]]:
    """
    Validate complete login form.
    
    Args:
        email: Email address
        password: Password
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not email or not password:
        return False, "Please enter both email and password."
    return True, None
