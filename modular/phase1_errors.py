"""
Error handling and logging utilities for Horizon Finder.

Provides consistent error messaging and logging across all modules.
"""

import logging
import streamlit as st
from typing import Optional, Callable, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module."""
    return logging.getLogger(name)


def handle_error(
    error: Exception,
    user_message: str = "An error occurred.",
    log_level: int = logging.ERROR,
    show_details: bool = False,
) -> None:
    """
    Handle an error consistently across the app.
    
    Args:
        error: The exception that occurred
        user_message: Message to display to the user
        log_level: Logging level for the error
        show_details: Whether to show technical details in UI
    """
    logger = get_logger("horizon_app")
    logger.log(log_level, f"{user_message} - {str(error)}", exc_info=True)
    
    if show_details:
        st.error(f"{user_message}\n\n`{str(error)}`")
    else:
        st.error(user_message)


def with_error_handling(
    default_return: Any = None,
    user_message: str = "An error occurred.",
    show_details: bool = False,
) -> Callable:
    """
    Decorator to wrap functions with consistent error handling.
    
    Args:
        default_return: Value to return if function fails
        user_message: Message to show user on error
        show_details: Whether to show technical details
    
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                handle_error(e, user_message, show_details=show_details)
                return default_return
        return wrapper
    return decorator


class ErrorContext:
    """Context manager for operation-specific error handling."""
    
    def __init__(self, operation_name: str, user_message: Optional[str] = None):
        self.operation_name = operation_name
        self.user_message = user_message or f"Failed to {operation_name}"
        self.logger = get_logger(f"horizon.{operation_name}")
    
    def __enter__(self):
        self.logger.info(f"Starting operation: {self.operation_name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.logger.error(
                f"Operation failed: {self.operation_name}",
                exc_info=(exc_type, exc_val, exc_tb)
            )
            handle_error(
                exc_val or Exception(str(exc_type)),
                self.user_message,
                show_details=False
            )
            return True  # Suppress the exception
        else:
            self.logger.info(f"Completed operation: {self.operation_name}")
        return False
