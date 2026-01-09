"""
Dependency Injection Container for managing service dependencies.

This module provides a simple DI container to manage service instances
and their dependencies across the application.
"""

from typing import Dict, Type, Any, Optional, TypeVar
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class Container:
    """Simple dependency injection container."""
    services: Dict[Type, Any] = field(default_factory=dict)
    singletons: Dict[Type, Any] = field(default_factory=dict)

    def register(self, interface: Type[T], implementation: Type[T], singleton: bool = True) -> None:
        """Register a service implementation."""
        if singleton:
            self.singletons[interface] = implementation
        else:
            self.services[interface] = implementation

    def register_instance(self, interface: Type[T], instance: T) -> None:
        """Register a service instance directly."""
        self.services[interface] = instance

    def get(self, interface: Type[T]) -> T:
        """Get a service instance."""
        # Check singletons first
        if interface in self.services:
            return self.services[interface]

        # Check registered classes
        if interface in self.singletons:
            impl_class = self.singletons[interface]
            instance = impl_class()
            self.services[interface] = instance
            return instance

        raise ValueError(f"Service {interface} not registered")


# Global container instance
container = Container()


def init_container(slack_client) -> Container:
    """Initialize the DI container with all services."""
    from ..services.attendance_service import AttendanceService
    from ..services.notification_service import NotificationService
    from ..jobs.scheduler import ReportScheduler
    from ..shared.logging_config import get_logger

    # Register services
    container.register_instance(type(slack_client), slack_client)

    # Services that need dependencies
    container.register(AttendanceService, AttendanceService)
    container.register(NotificationService, lambda: NotificationService(slack_client))
    container.register(ReportScheduler, ReportScheduler)

    logger.info("DI container initialized")
    return container


def get_service(interface: Type[T]) -> T:
    """Get a service from the global container."""
    return container.get(interface)