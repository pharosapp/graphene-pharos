from functools import partial

from django.db.models.query import QuerySet

from promise import Promise

from graphene import Int, NonNull
from graphene.types import Field, List

from .settings import graphene_settings
from .utils import maybe_queryset


class DjangoListField(Field):
    def __init__(self, _type, *args, **kwargs):
        from .types import DjangoObjectType

        if isinstance(_type, NonNull):
            _type = _type.of_type

        # Django would never return a Set of None  vvvvvvv
        super().__init__(List(NonNull(_type)), *args, **kwargs)

        assert issubclass(
            self._underlying_type, DjangoObjectType
        ), "DjangoListField only accepts DjangoObjectType types"

    @property
    def _underlying_type(self):
        _type = self._type
        while hasattr(_type, "of_type"):
            _type = _type.of_type
        return _type

    @property
    def model(self):
        return self._underlying_type._meta.model

    def get_manager(self):
        return self.model._default_manager

    @staticmethod
    def list_resolver(
        django_object_type, resolver, default_manager, root, info, **args
    ):
        queryset = maybe_queryset(resolver(root, info, **args))
        if queryset is None:
            queryset = maybe_queryset(default_manager)

        if isinstance(queryset, QuerySet):
            # Pass queryset to the DjangoObjectType get_queryset method
            queryset = maybe_queryset(django_object_type.get_queryset(queryset, info))

        return queryset

    def wrap_resolve(self, parent_resolver):
        resolver = super().wrap_resolve(parent_resolver)
        _type = self.type
        if isinstance(_type, NonNull):
            _type = _type.of_type
        django_object_type = _type.of_type.of_type
        return partial(
            self.list_resolver,
            django_object_type,
            resolver,
            self.get_manager(),
        )