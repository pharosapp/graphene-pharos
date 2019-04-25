import enum
from functools import partial
from graphene.types.field import Field
from graphene_django.filter.utils import get_filtering_args_from_filterset, get_filterset_class
from graphene.utils.str_converters import to_snake_case, to_camel_case
from graphql.error import GraphQLError
import graphene
from ..fields import DjangoListField
from ..utils import maybe_queryset


DEFAULT_ORDER = 'id'

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


class OrderingDirectionEnum(enum.Enum):
    ASC = 1
    DESC = 2

OrderingDirectionEnumType = graphene.Enum.from_enum(OrderingDirectionEnum)

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
        })

        kwargs['args']['order_by'] = graphene.List(OrderByEnumObject, default_value=[], name='orderBy').Argument()
        kwargs['args']['limit'] = graphene.Int(default_value=0, name='limit').Argument()
        kwargs['args']['offset'] = graphene.Int(default_value=0, name='offset').Argument()
        kwargs['args'].update(self.filtering_args)
        return kwargs

    def filter(self, _type, info, kwargs):
        limit = kwargs.pop('limit')
        offset = kwargs.pop('offset')
        order_by_enum = kwargs.pop('order_by')

        # for some reason enums doesn't get converted so do it manually
        for i, to_enum in enumerate(order_by_enum):
            order_by_enum[i] = {
                'field': self.order_by_enum(to_enum['field']),
                'direction': OrderingDirectionEnum(to_enum['direction']),
            }

        order_by = [
            ('-' if o['direction'] == OrderingDirectionEnum.DESC else '') + to_snake_case(o['field'].name)
            for o in order_by_enum
        ]

        filter_kwargs = {k: v
            for k, v in kwargs.items()
            if k in self.filtering_args
        }

        user = info.context.user
        permission = _type._meta.permission_class()
        qs = permission.viewable(user, info=info)
        qs = qs.order_by(*order_by, DEFAULT_ORDER)

        qs = self.filterset_class(data=filter_kwargs, queryset=qs, request=info.context).qs

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
            page_info = graphene.Field(PageInfo)

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