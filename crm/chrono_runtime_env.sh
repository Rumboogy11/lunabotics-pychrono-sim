#!/usr/bin/env bash
# Prefer locally installed Chrono libraries over stale copies in chrono-env.
# Source this from the CRM launcher after PROJECT_ROOT has been set.

chrono_use_local_runtime() {
    local lib_dir="$PROJECT_ROOT/chrono-install/lib"
    [[ -d "$lib_dir" ]] || return 0

    # chrono-env/bin/python has a DT_RPATH pointing at chrono-env/lib.  Old-style
    # DT_RPATH is searched before LD_LIBRARY_PATH, so an older libChrono_core.so
    # there can shadow the local build even when LD_LIBRARY_PATH is correct.
    # LD_PRELOAD is searched before both and pins the Python bindings to the
    # installed libraries they were built against.
    local libs=()
    local lib
    for lib in \
        libChrono_core.so \
        libChrono_fsi.so \
        libChrono_fsisph.so \
        libChrono_vehicle.so \
        libChronoModels_vehicle.so \
        libChronoModels_robot.so \
        libChrono_parsers.so \
        libyaml-cpp.so.0.8.0; do
        if [[ -e "$lib_dir/$lib" ]]; then
            libs+=("$lib_dir/$lib")
        fi
    done
    ((${#libs[@]})) || return 0

    local joined
    joined=$(IFS=:; echo "${libs[*]}")
    export LD_PRELOAD="${joined}${LD_PRELOAD:+:${LD_PRELOAD}}"
}
