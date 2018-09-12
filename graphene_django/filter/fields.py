from functools import partial
from graphene.types.field import Field
from graphene_django.filter.utils import get_filtering_args_from_filterset, get_filterset_class
from graphene.utils.str_converters import to_snake_case
from graphql.error import GraphQLError
import graphene
from ..fields import DjangoListField
from ..utils import maybe_queryset
from django_filters.constants import STRICTNESS


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


class FilterBase():
    def get_filter_args(self, _type, kwargs):
        self.filterset_class = get_filterset_class(
            getattr(_type._meta, 'filterset_class', None),
            model=_type._meta.model,
            fields=_type._meta.filter_fields,
        )

        self.filtering_args = get_filtering_args_from_filterset(
            self.filterset_class, _type)
        kwargs.setdefault('args', {})
        kwargs['args']['order_by'] = graphene.List(graphene.String, default_value=[DEFAULT_ORDER], name='orderBy').Argument()
        kwargs['args'].update(self.filtering_args)
        return kwargs

    def filter(self, _type, info, kwargs):
        order_by = [to_snake_case(o) for o in kwargs.pop('order_by')]
        permission = _type._meta.permission_class()

        for k in kwargs:
            if k not in self.filtering_args:
                    raise GraphQLError('filter argument not in filter class')

        filter_kwargs = {k: v
            for k, v in kwargs.items()
            if k in self.filtering_args
        }

        user = info.context.user
        qs = permission.viewable(user, info=info)
        qs = self.filterset_class(data=filter_kwargs, queryset=qs, request=info.context, strict=STRICTNESS.RAISE_VALIDATION_ERROR).qs
        for o in order_by:
            o = o.replace('-', '')
            if o != DEFAULT_ORDER and o not in self.filtering_args:
                    raise GraphQLError('order_by argument "%s" not in filter class' % o)

        qs = qs.order_by(*order_by)
        return qs


class DjangoFilterField(Field, FilterBase):
    '''
    Custom field to use django-filter with graphene object types (without relay).
    '''
    def __init__(self, _type, *args, **kwargs):
        kwargs['args'] = {
            'limit': graphene.Int(default_value=10, name='limit').Argument(),
            'offset': graphene.Int(default_value=0, name='offset').Argument(),
        }
        kwargs = self.get_filter_args(_type, kwargs)

        class ListBase(graphene.ObjectType):
            def __init__(self, type, queryset, has_next_page, total, *args, **kwargs):
                self.queryset = queryset
                self.has_next_page = has_next_page
                self.total = total
                return super().__init__(*args, **kwargs)
            
            class Meta:
                name = _type.__name__ + "ListBase"


            objects = graphene.List(_type)
            page_info = graphene.Field(PageInfo)

            def resolve_objects(self, resolve_info, **kwargs):
                return self.queryset
                
            def resolve_page_info(self, resolve_info, **kwargs):
                return PageInfo(has_next_page=self.has_next_page, total=self.total) 
        
        # _type.django_filter_field = self

        self.of_type = ListBase
        self.inner_type = _type
        super().__init__(ListBase, *args, **kwargs)
     

    def field_resolver(self, root, info, *args, **kwargs):
        limit = kwargs.pop('limit')
        offset = kwargs.pop('offset')
        
        qs = self.filter(self.inner_type, info, kwargs)

        total = qs.count()
        qs = qs[offset: offset+limit]
        has_next_page = qs.count() == limit

        return self.of_type(type=self.of_type, queryset=qs, has_next_page=has_next_page, total=total)

    def get_resolver(self, parent_resolver):
        return self.field_resolver

    # @classmethod
    #  def create_page_info(cls, _type):
    # 	  def init(self, has_next_page, total, *args, **kwargs):
    # 			self._has_next_page = has_next_page
    # 			self._total = total
    # 			super(type(self), self).__init__(*args, **kwargs)

    # 	  def resolve_has_next_page(self, info, **kwargs):
    # 			return self._has_next_page
          
    # 	  def resolve_total(self, info, **kwargs):
    # 			return self._total

    # 	  return type(
    # 			_type.__name__ + "PageInfo",
    # 			(graphene.ObjectType,), {
    # 				 '__init__': init,
    # 				 'has_next_page': graphene.Boolean(),
    # 				 'total': graphene.Int(),
    # 				 'resolve_has_next_page': resolve_has_next_page,
    # 				 'resolve_total': resolve_total,
    # 			}
    # 	  )

    #  @classmethod
    #  def create_base_object_type(cls, _type):
    # 	  page_info_class = cls.create_page_info(_type)
    # 	  def init(self, queryset, has_next_page, total, *args, **kwargs):
    # 			self.queryset = queryset
    # 			self.has_next_page = has_next_page
    # 			self.total = total
    # 			return super(type(self), self).__init__(*args, **kwargs)

    # 	  def resolve_objects(self, resolve_info, **kwargs):
    # 			return self.queryset
    # 	  def resolve_page_info(self, resolve_info, **kwargs):
    # 			return page_info_class(has_next_page=self.has_next_page, total=self.total)
          
    # 	  return type(
    # 			 _type.__name__ + "ListBase",
    # 			 (graphene.ObjectType,),
    # 			 {
    # 				 '__init__': init,
    # 				 'objects': graphene.List(_type),
    # 				 'page_info': graphene.Field(page_info_class),
    # 				 'resolve_objects': resolve_objects,
    # 				 'resolve_page_info': resolve_page_info


    # 			 }
    # 	  )


class DjangoInnerListField(Field, FilterBase):
    def __init__(self, _type, *args, **kwargs):
        kwargs = self.get_filter_args(_type, kwargs)
        self.inner_type = _type
        super().__init__(graphene.List(_type), *args, **kwargs)

    @property
    def model(self):
        return self.type.of_type._meta.node._meta.model

    def list_resolver(self, resolver, root, info, **kwargs):
        return maybe_queryset(resolver(root, info, **kwargs)) & self.filter(self.inner_type, info, kwargs)
        # return maybe_queryset(resolver(root, info, **kwargs))

    def get_resolver(self, parent_resolver):
        return partial(self.list_resolver, parent_resolver)