import enum
from functools import partial
import graphene
from graphene.types.field import Field
from graphene_django.filter.utils import get_filtering_args_from_filterset, get_filterset_class
from graphene.utils.str_converters import to_snake_case, to_camel_case
from graphql.error import GraphQLError
from django.db.models.functions import Lower
from django.db.models import F 

from django.core.exceptions import ValidationError

from graphene.types.enum import EnumType
from graphene.types.argument import to_arguments
from graphene.utils.str_converters import to_snake_case

from .utils import get_filtering_args_from_filterset, get_filterset_class
from ..fields import DjangoListField
from ..utils import maybe_queryset

DEFAULT_ORDER = 'id'


def convert_enum(data):
    """
    Check if the data is a enum option (or potentially nested list of enum option)
    and convert it to its value.

    This method is used to pre-process the data for the filters as they can take an
    graphene.Enum as argument, but filters (from django_filters) expect a simple value.
    """
    if isinstance(data, list):
        return [convert_enum(item) for item in data]
    if isinstance(type(data), EnumType):
        return data.value
    else:
        return data



class PageInfo(graphene.ObjectType):
    def __init__(self, has_next_page, total, *args, **kwargs):
        self._has_next_page = has_next_page
        self._total = total
        super().__init__(*args, **kwargs)
    
    has_next_page = graphene.Boolean()
    total = graphene.Int()

    def resolve_has_next_page(self, info, **kwargs):
        return self._has_next_page
    
    def resolve_total(self, info, **kwargs):
        return self._total 

    @property
    def filtering_args(self):
        if not self._filtering_args:
            self._filtering_args = get_filtering_args_from_filterset(
                self.filterset_class, self.node_type
            )
        return self._filtering_args

    @classmethod
    def resolve_queryset(
        cls, connection, iterable, info, args, filtering_args, filterset_class
    ):
        def filter_kwargs():
            kwargs = {}
            for k, v in args.items():
                if k in filtering_args:
                    if k == "order_by" and v is not None:
                        v = to_snake_case(v)
                    kwargs[k] = convert_enum(v)
            return kwargs

        qs = super().resolve_queryset(connection, iterable, info, args)

        filterset = filterset_class(
            data=filter_kwargs(), queryset=qs, request=info.context
        )
        if filterset.is_valid():
            return filterset.qs
        raise ValidationError(filterset.form.errors.as_json())

    def get_queryset_resolver(self):
        return partial(
            self.resolve_queryset,
            filterset_class=self.filterset_class,
            filtering_args=self.filtering_args,
        )

class OrderingDirectionEnum(enum.Enum):
    ASC = 1
    DESC = 2

class OrderingModifierEnum(enum.Enum):
    CASE_INSENSITIVE = 1


OrderingDirectionEnumType = graphene.Enum.from_enum(OrderingDirectionEnum)
OrderingModifierEnumType = graphene.Enum.from_enum(OrderingModifierEnum)

class FilterBase():
    def get_filter_args(self, _type, kwargs, inner_field=None):
        self.filterset_class = get_filterset_class(
            getattr(_type._meta, 'filterset_class', None),
            model=_type._meta.model,
            fields=_type._meta.filter_fields,
        )

        self.filtering_args = get_filtering_args_from_filterset(
            self.filterset_class, _type)
        kwargs.setdefault('args', {})

        order_args = {to_camel_case(k): i for (i, k) in enumerate(set(_type._meta.order_fields + ['id']))}

        if len(order_args.items()) == 0:
            raise Exception(f'No ordering args found on {_type}')

        order_by_enum = enum.Enum(
            f'{_type}_{inner_field.model.__name__}_{inner_field.name}_OrderingFilter' if inner_field else f'{_type}OrderingFilter',
            order_args
        )
        self.order_by_enum = order_by_enum

        OrderByEnumObject = type(order_by_enum.__name__ + 'Object', (graphene.InputObjectType,), {
            'field': graphene.Enum.from_enum(order_by_enum)(),
            'direction': OrderingDirectionEnumType(default_value=OrderingDirectionEnum.ASC.value),
            'modifiers': graphene.List(OrderingModifierEnumType, default_value=[]),
        })

        kwargs['args']['order_by'] = graphene.List(OrderByEnumObject, default_value=[], name='orderBy').Argument()
        kwargs['args']['limit'] = graphene.Int(default_value=0, name='limit').Argument()
        kwargs['args']['offset'] = graphene.Int(default_value=0, name='offset').Argument()
        kwargs['args'].update(self.filtering_args)
        return kwargs

    def get_order_by(self, order_by_args):
        order_by_enum = []
        for to_enum in order_by_args:
        # for some reason enums doesn't get converted so do it manually
            order_by_enum.append({
                'field': self.order_by_enum(to_enum['field']),
                'direction': OrderingDirectionEnum(to_enum['direction']),
                'modifiers': [OrderingModifierEnum(m) for m in to_enum['modifiers']],
            })
        order_by = []
        for o in order_by_enum:
            desc = o['direction'] == OrderingDirectionEnum.DESC
            field = to_snake_case(o['field'].name)
            order_field = F(field)
            for modifier in o['modifiers']:
                if modifier == OrderingModifierEnum.CASE_INSENSITIVE:
                    order_field = Lower(order_field)
                else:
                    raise Exception(f"Ordering modifier `{modifier}` does not exist")
            order_by.append(
                order_field.desc() if desc else order_field.asc()
            )
        return order_by

    def filter(self, _type, info, kwargs):
        limit = kwargs.pop('limit')
        offset = kwargs.pop('offset')
        order_by_args = kwargs.pop('order_by')

        filter_kwargs = {k: v
            for k, v in kwargs.items()
            if k in self.filtering_args
        }

        user = info.context.user
        permission = _type._meta.permission_class()
        qs = permission.viewable(user, info=info)
        qs = self.filterset_class(data=filter_kwargs, queryset=qs, request=info.context).qs
        qs = qs.order_by(
            *self.get_order_by(order_by_args),
            DEFAULT_ORDER,
        )

        qs = qs[offset: offset+limit] if limit else qs[offset:]

        return qs, limit


class DjangoFilterField(Field, FilterBase):
    '''
    Custom field to use django-filter with graphene object types (without relay).
    '''
    def __init__(self, _type, *args, **kwargs):
        kwargs = self.get_filter_args(_type, kwargs)

        class ListBase(graphene.ObjectType):
            def __init__(self, type, queryset, has_next_page, total, *args, **kwargs):
                self.queryset = queryset
                self.has_next_page = has_next_page
                self.total = total
                return super().__init__(*args, **kwargs)
            
            class Meta:
                name = _type.__name__ + "ListBase"

            objects = graphene.NonNull(graphene.List(graphene.NonNull(_type)))
            page_info = graphene.NonNull(PageInfo)

            def resolve_objects(self, resolve_info, **kwargs):
                return self.queryset
                
            def resolve_page_info(self, resolve_info, **kwargs):
                return PageInfo(has_next_page=self.has_next_page, total=self.total) 
        
        self.of_type = ListBase
        self.inner_type = _type
        super().__init__(ListBase, *args, **kwargs)

    def field_resolver(self, root, info, *args, **kwargs):
        qs, limit = self.filter(self.inner_type, info, kwargs)

        total = qs.count()
        has_next_page = total == limit

        return self.of_type(type=self.of_type, queryset=qs, has_next_page=has_next_page, total=total)

    def get_resolver(self, parent_resolver):
        return self.field_resolver


class DjangoInnerListField(Field, FilterBase):
    def __init__(self, _type, *args, inner_field=None, **kwargs):
        kwargs = self.get_filter_args(_type, kwargs, inner_field=inner_field)
        self.inner_type = _type
        super().__init__(graphene.NonNull(graphene.List(graphene.NonNull(_type))), *args, **kwargs)

    @property
    def model(self):
        return self.type.of_type._meta.node._meta.model
    
    def list_resolver(self, resolver, root, info, **kwargs):
        qs, limit = self.filter(self.inner_type, info, kwargs)
        return maybe_queryset(resolver(root, info, **kwargs)) & qs

    def get_resolver(self, parent_resolver):
        return partial(self.list_resolver, parent_resolver)