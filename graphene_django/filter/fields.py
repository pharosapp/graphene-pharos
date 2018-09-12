from graphene.types.field import Field
from .filter.utils import get_filtering_args_from_filterset, get_filterset_class
from graphene.utils.str_converters import to_snake_case
from graphql.error import GraphQLError
import graphene


DEFAULT_ORDER = 'id'

class DjangoFilterField(Field):
    '''
    Custom field to use django-filter with graphene object types (without relay).
    '''
    def __init__(self, _type, permission_class, fields=None, extra_filter_meta=None,
            ilterset_class=None, *args, **kwargs):
          
        self.permission_class = permission_class
        _fields = _type._meta.filter_fields
        _model = _type._meta.model
        # self.fields = fields or _fields
        self.filterset_class = get_filterset_class(filterset_class, model=_model, fields=_fields)
        self.filtering_args = get_filtering_args_from_filterset(
            self.filterset_class, _type
        )

        kwargs['args'] = {
            'limit': graphene.Int(default_value=10, name='limit').Argument(),
            'offset': graphene.Int(default_value=0, name='offset').Argument(),
            'order_by': graphene.List(graphene.String,default_value=[DEFAULT_ORDER], name='orderBy').Argument(),
        }
        kwargs['args'].update(self.filtering_args)

        self.of_type = self.create_base_object_type(_type)
        super().__init__(self.of_type, *args, **kwargs)
     
     
    @classmethod
    def create_page_info(cls, _type):
        def init(self, has_next_page, total, *args, **kwargs):
                self._has_next_page = has_next_page
                self._total = total
                super(type(self), self).__init__(*args, **kwargs)

        def resolve_has_next_page(self, info, **kwargs):
            return self._has_next_page
          
        def resolve_total(self, info, **kwargs):
            return self._total

        return type(
            _type.__name__ + "PageInfo",
            (graphene.ObjectType,), {
                '__init__': init,
                'has_next_page': graphene.Boolean(),
                'total': graphene.Int(),
                'resolve_has_next_page': resolve_has_next_page,
                'resolve_total': resolve_total,
            }
        )

    @classmethod
    def create_base_object_type(cls, _type):
        page_info_class = cls.create_page_info(_type)
        def init(self, queryset, has_next_page, total, *args, **kwargs):
            self.queryset = queryset
            self.has_next_page = has_next_page
            self.total = total
            return super(type(self), self).__init__(*args, **kwargs)

        def resolve_objects(self, resolve_info, **kwargs):
            return self.queryset
        def resolve_page_info(self, resolve_info, **kwargs):
            return page_info_class(has_next_page=self.has_next_page, total=self.total)
          
        return type(
            _type.__name__ + "ListBase",
            (graphene.ObjectType,),
            {
                '__init__': init,
                'objects': graphene.List(_type),
                'page_info': graphene.Field(page_info_class),
                'resolve_objects': resolve_objects,
                'resolve_page_info': resolve_page_info


            }
        )

    def field_resolver(self, root, info, *args, **kwargs):
        limit = kwargs.pop('limit')
        offset = kwargs.pop('offset')
        order_by = [to_snake_case(o) for o in kwargs.pop('order_by')]

        user = info.context.user
        permission = self.permission_class()
        for k, v in kwargs.items():
            if k not in self.filtering_args:
                raise GraphQLError('filter argument not in filter class')
        filter_kwargs = {k: v
            for k, v in kwargs.items()
            if k in self.filtering_args
        }

        qs = permission.viewable(user, info=info)
        qs = self.filterset_class(data=filter_kwargs, queryset=qs, request=info.context).qs

        for o in order_by:
            if o != DEFAULT_ORDER and o not in self.filtering_args:
                raise GraphQLError('order_by argument "%s" not in filter class' % o)

        qs = qs.order_by(*order_by)

        total = qs.count()

        qs = qs[offset: offset+limit]

        has_next_page = qs.count() == limit
        return self.of_type(queryset=qs, has_next_page=has_next_page, total=total)

     def get_resolver(self, parent_resolver):
        return self.field_resolver