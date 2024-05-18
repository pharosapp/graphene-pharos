import itertools

from django.db import models
from django_filters.filterset import BaseFilterSet, FilterSet, Filter, ModelMultipleChoiceFilter
from django_filters.filterset import FILTER_FOR_DBFIELD_DEFAULTS

# from .filters import GlobalIDFilter, GlobalIDMultipleChoiceFilter
from graphql_relay.node.node import from_global_id

from ..forms import GlobalIDFormField, GlobalIDMultipleChoiceField


class GlobalIDFilter(Filter):
    field_class = GlobalIDFormField

    def filter(self, qs, value):
        """ Convert the filter value to a primary key before filtering """
        return super(GlobalIDFilter, self).filter(qs, value)


class GlobalIDMultipleChoiceFilter(ModelMultipleChoiceFilter):
    field_class = GlobalIDMultipleChoiceField

    def filter(self, qs, value):
        gids = value # [from_global_id(v)[1] for v in value] don't use relay's id conversion
        return super(GlobalIDMultipleChoiceFilter, self).filter(qs, gids)


GRAPHENE_FILTER_SET_OVERRIDES = {
    models.AutoField: {"filter_class": GlobalIDFilter},
    models.OneToOneField: {"filter_class": GlobalIDFilter},
    models.ForeignKey: {"filter_class": GlobalIDFilter},
    models.ManyToManyField: {"filter_class": GlobalIDMultipleChoiceFilter},
    models.ManyToOneRel: {"filter_class": GlobalIDMultipleChoiceFilter},
    models.ManyToManyRel: {"filter_class": GlobalIDMultipleChoiceFilter},
}


class GrapheneFilterSetMixin(BaseFilterSet):
    """A django_filters.filterset.BaseFilterSet with default filter overrides
    to handle global IDs"""

    FILTER_DEFAULTS = dict(
        itertools.chain(
            FILTER_FOR_DBFIELD_DEFAULTS.items(), GRAPHENE_FILTER_SET_OVERRIDES.items()
        )
    )


def setup_filterset(filterset_class):
    """Wrap a provided filterset in Graphene-specific functionality"""
    return type(
        f"Graphene{filterset_class.__name__}",
        (filterset_class, GrapheneFilterSetMixin),
        {},
    )


def custom_filterset_factory(model, filterset_base_class=FilterSet, **meta):
    """Create a filterset for the given model using the provided meta data"""
    meta.update({"model": model})
    meta_class = type("Meta", (object,), meta)
    filterset = type(
        str("%sFilterSet" % model._meta.object_name),
        (filterset_base_class, GrapheneFilterSetMixin),
        {"Meta": meta_class},
    )
    return filterset
