from graphene.relay import Node

class DjangoNode(Node):
	class Meta:
		name = 'Node'

	@staticmethod
	def to_global_id(type, id):
		return id
	
	@classmethod
	def from_global_id(cls, global_id):
		return global_id

	@staticmethod
	def get_node_from_global_id(id, context, info, only_type=None):
		return info.return_type.graphene_type._meta.model.objects.get(id=id)