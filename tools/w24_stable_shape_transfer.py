"""Required native shape-transfer contract. No analytic or interpolated substitute."""
import math

SCHEMA='W24_STABLE_GEL_SHAPE_TRANSFER_REQUIREMENTS_V1'
REQUIRED_BASELINES=('baseline-flat','baseline-step')
REQUIRED_NATIVE_ARTIFACTS=('raw_phase_field_grid','accepted_native_times','full_history_volume',
    'final_native_profile','contact_history','wall_segments','native_stability_window',
    'sensitivity_convergence','model_ref_revision_worker_epoch','immutable_operations_origin')

class StableShapeTransferError(ValueError): pass

def stable_shape_transfer_requirements():
    return {'schema':SCHEMA,'status':'NEEDS_AUTHENTICATED_NATIVE_CONVERGED_FLAT_STEP_BASELINES',
        'baseline_cases':list(REQUIRED_BASELINES),'required_artifacts':list(REQUIRED_NATIVE_ARTIFACTS),
        'original_sensitivity_slots':14,'original_volume_relative_error_limit':1e-3,
        'original_stability_window_Tc':4.0,'original_height_contact_travel_epsilon_limit':0.25,
        'original_maximum_speed_sigma_over_mu_limit':1e-3,
        'original_profile_difference_R_limit':0.02,'original_contact_distance_epsilon_limit':0.5,
        'original_common_wetted_segment_minimum':3,'original_common_wetted_fraction_minimum':0.95,
        'projection_required':{'source_geometry':'exact_native_wall_segments_and_profile',
            'target_geometry':'fresh_cure_model_bound_to_new_ModelRef_revision_WorkerEpoch',
            'approximation':'EXPLICIT_METHOD_AND_ERROR_REQUIRED_BEFORE_ACCEPTANCE',
            'provenance':'BOTH_SOURCE_AND_TARGET_RAW_ARTIFACTS_REQUIRED'},
        'analytic_spherical_cap':'FORBIDDEN_AS_NATIVE_STABILITY_SUBSTITUTE',
        'guessed_wall':'FORBIDDEN','interpolated_convergence':'FORBIDDEN',
        'native_shape_available':False,'native_admission':'REFUSED_SOURCE_CONTRACT_ONLY',
        'science_acceptance':'NOT_ACCEPTED','Study_solver_authorized':0}

def validate_source_transfer_declaration(declaration):
    """Check a future handoff declaration; never turns caller data into authority."""
    if not isinstance(declaration,dict) or set(declaration)!={'schema','baselines','projection'} or declaration['schema']!=SCHEMA:
        raise StableShapeTransferError('closed transfer declaration schema required')
    cases=declaration['baselines']
    if not isinstance(cases,dict) or set(cases)!=set(REQUIRED_BASELINES):
        raise StableShapeTransferError('both original flat/step native baselines required')
    for case in cases.values():
        if not isinstance(case,dict) or set(case)!=set(REQUIRED_NATIVE_ARTIFACTS):
            raise StableShapeTransferError('all raw native artifacts and source context required')
        # References, not embedded/generated numerical grids. Later admission
        # must authenticate each referenced byte against actual managed origins.
        for name,row in case.items():
            if not isinstance(row,dict) or set(row)!={'path','bytes','sha256'} or not isinstance(row['path'],str) or not row['path'].startswith('/') or type(row['bytes']) is not int or row['bytes']<=0 or not isinstance(row['sha256'],str) or len(row['sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['sha256']):
                raise StableShapeTransferError('ordinary byte-bound raw reference required for '+name)
    projection=declaration['projection']
    if not isinstance(projection,dict) or set(projection)!={'method','error_metric','error_value','source_context','target_context'}:
        raise StableShapeTransferError('explicit projection approximation/error and both contexts required')
    if not all(isinstance(projection[k],str) and projection[k].strip() for k in ('method','error_metric')) or type(projection['error_value']) not in (int,float) or not math.isfinite(projection['error_value']) or projection['error_value']<0:
        raise StableShapeTransferError('finite explicit projection error required')
    for name in ('source_context','target_context'):
        context=projection[name]
        if not isinstance(context,dict) or set(context)!={'model_ref','revision','worker_epoch'} or not isinstance(context['model_ref'],dict) or not context['model_ref'] or type(context['revision']) is not int or context['revision']<0 or not isinstance(context['worker_epoch'],dict) or not context['worker_epoch']:
            raise StableShapeTransferError('both exact model/revision/Worker context structures required')
    if projection['source_context']==projection['target_context']:
        raise StableShapeTransferError('historical source context cannot be reused as fresh cure target')
    return {'schema':SCHEMA,'status':'SOURCE_DECLARATION_ONLY_NATIVE_BYTE_AUTHENTICATION_AND_CONVERGENCE_REQUIRED',
        'native_shape_available':False,'native_admission':'REFUSED','Study_solver_authorized':0,
        'science_acceptance':'NOT_ACCEPTED'}
