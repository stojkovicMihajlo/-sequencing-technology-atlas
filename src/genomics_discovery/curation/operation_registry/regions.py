"""Find recorded component witnesses inside larger regions; infer no new mapping."""
from ..workflow_extraction.models import Workflow, digest
from .integrity import validate_snapshot


def components_in_region(snapshot: dict, workflow: Workflow, operation_ids=(), *, module=None):
    validate_snapshot(snapshot)
    if bool(operation_ids) == bool(module):
        raise ValueError('Supply explicit operation IDs or one module ID')
    if module:
        modules = {m.id:m for m in workflow.modules}
        if module not in modules:raise ValueError('Unknown module ID')
        operation_ids = modules[module].operation_ids
    operations = {o.id:o for o in workflow.operations}
    selected = set(operation_ids)
    if len(selected)!=len(operation_ids) or selected-operations.keys():
        raise ValueError('Region has duplicate or unknown operations')
    workflow_hash = digest(workflow)
    contained, crossing = [], []
    bound_count = 0
    for aid,row in sorted(snapshot['assignments'].items()):
        if not row.get('admission_decision_sha256'):continue
        occurrence = row['occurrence']
        if occurrence['workflow_sha256']!=workflow_hash:continue
        bound_count += 1
        witness = {o['id'] for o in occurrence['operations']}
        if any(o['id'] not in operations or o!=operations[o['id']].model_dump(mode='json')
               for o in occurrence['operations']):
            raise ValueError('Stored occurrence operations do not match the bound workflow')
        if occurrence['method_id']!=workflow.method_id:
            raise ValueError('Stored occurrence method identity differs from workflow')
        overlap = witness & selected
        if not overlap:continue
        concept = snapshot['concepts'][row['concept_id']]
        record = {'concept_id':row['concept_id'],'relation':concept['relation'],
            'definition':concept['definition'],'assignment_id':aid,
            'occurrence_id':occurrence['id'],'occurrence_sha256':row['occurrence_sha256'],
            'witness_operation_ids':sorted(witness),'inside_region':sorted(overlap),
            'outside_region':sorted(witness-selected),
            'inputs':occurrence['inputs'],'outputs':occurrence['outputs'],
            'requirements':occurrence['requirements'],
            'source_packet_sha256':occurrence['source_packet_sha256'],
            'mapping_review_sha256':row['review_sha256'],
            'parent_review_status':occurrence['workflow_review_status']}
        (contained if witness<=selected else crossing).append(record)
    return {'format_version':'region_component_lookup/1.0','workflow_sha256':workflow_hash,
        'registry_sha256':digest(snapshot),'operation_ids':sorted(selected),'module_id':module,
        'contained_components':contained,'boundary_crossing_components':crossing,
        'bound_assignment_count':bound_count,'new_mapping_assertions':0,
        'source_bytes_reopened':False,
        'boundary':'Navigation over archived admitted mappings, not a fresh source check. '
        'Contained witnesses do not make the entire region equivalent to a concept. '
        'A boundary-crossing witness is incomplete in this region. No recorded match '
        'does not establish absence, and no compatibility, ancestry or novelty is inferred.'}
