import ctypes,json
from comsol_mcp._w26_memory_resources import _DarwinMemoryApi,_DarwinVmStatsRev0
api=_DarwinMemoryApi();lib=api.lib
host=lib.mach_host_self()
try:
 page=ctypes.c_uint32();pr=lib.host_page_size(host,ctypes.byref(page))
 stats=_DarwinVmStatsRev0(); count=ctypes.c_uint32(ctypes.sizeof(stats)//4)
 kr=lib.host_statistics64(host,4,ctypes.cast(ctypes.byref(stats),ctypes.POINTER(ctypes.c_int32)),ctypes.byref(count))
 print(json.dumps({'scope':'READ_ONLY_MACH_SAMPLE_NO_BOOT_ATTESTATION_NO_ADMISSION_UNLOCK','page_size_result':pr,'page_size':page.value,'kern_return':kr,'count':count.value,'struct_size':ctypes.sizeof(stats),'free_count':stats.free_count,'available_free_only_bytes':stats.free_count*page.value,'active_count':stats.active_count,'inactive_count':stats.inactive_count},sort_keys=True))
finally:
 task=int(ctypes.c_uint32.in_dll(lib,'mach_task_self_').value)
 assert lib.mach_port_deallocate(task,host)==0
