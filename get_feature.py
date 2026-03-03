import re
import numpy as np

def get_structure_prop(text):
    bonds = [] #Bohr
    angel = [] #Radian
    DA = []  #Radian
    try:
        pattern = r'Redundant Internal Coordinates \(Bohr and Radian\)\s*\n\s*\n\s*Definition\s+dE/dq\s+Value\s+Step\s+New-Value\s*\n\s*-{2,}\s*\n([\s\S]*?)\s*-{2,}'
        structure_lines = re.findall(pattern, text, re.MULTILINE)
        for line in structure_lines[-1].splitlines():
            line = line.split()
            if line[1] == 'R':
                bonds.append(float(line[-2]))
            elif line[1] == 'A':
                angel.append(float(line[-2]))
            elif line[1] == 'D':
                DA.append(float(line[-2]))
        av_structure = {
            'bonds': np.mean(bonds) * 0.529177,  # convert Bohr to Angstrom
            'angles': np.mean(angel) * (180.0 / np.pi),  # convert Radian to Degree
            'DA': np.mean(np.abs(DA)) * (180.0 / np.pi)  # convert Radian to Degree
                        }
    except Exception as e:
        av_structure = None
        print(f"Error in parsing structure properties: {e}")

    return av_structure

def calculate_structure_prop(args,type_calc):
    # 1. get natural cutoffs
    from ase.neighborlist import natural_cutoffs, NeighborList
    try:
        atoms=io.read(f"{args.workdir}/{type_calc}/{type_calc}ed.xyz")
    except Exception as e:
        ValueError(f"Failed to read structure file for {type_calc} calculation. Error: {str(e)}")

    cutoffs = natural_cutoffs(atoms)
    
    # 2. build NeighborList
    nl = NeighborList(cutoffs, self_interaction=False, bothways=True)
    nl.update(atoms)

    # store results
    bond_lengths = []
    bond_angles = []
    dihedral_angles = []

    # denay repetition in counting
    seen_bonds = set()
    seen_angles = set()
    seen_dihedrals = set()

    # get number of atoms
    n_atoms = len(atoms)

    # === step A: Adjacency List ===
    adjacency = {i: [] for i in range(n_atoms)}
    for i in range(n_atoms):
        indices, offsets = nl.get_neighbors(i)
        for idx in indices:
            adjacency[i].append(idx)

    # === step B calculation ===
    # 1. Bonds
    for i in range(n_atoms):
        for j in adjacency[i]:
            bond_pair = tuple(sorted((i, j)))
            if bond_pair not in seen_bonds:
                dist = atoms.get_distance(i, j)
                bond_lengths.append(dist)
                seen_bonds.add(bond_pair)
    # 2. Angles
    for j in range(n_atoms):
        neighbors = adjacency[j]
        if len(neighbors) < 2:
            continue

        for idx1 in range(len(neighbors)):
            for idx2 in range(idx1 + 1, len(neighbors)):
                i = neighbors[idx1]
                k = neighbors[idx2]
                
                angle_triplet = (min(i, k), j, max(i, k))
                
                if angle_triplet not in seen_angles:
                    angle = atoms.get_angle(i, j, k, mic=False)
                    bond_angles.append(angle)
                    seen_angles.add(angle_triplet)

    # 3. Dihedrals
    for bond in seen_bonds:
        j, k = bond 
        
        neighbors_j = [n for n in adjacency[j] if n != k]
        neighbors_k = [n for n in adjacency[k] if n != j]

        for i in neighbors_j:
            for l in neighbors_k:
                if i < l:
                    idx_seq = (i, j, k, l)
                else:
                    idx_seq = (l, k, j, i)
                
                if idx_seq not in seen_dihedrals:
                    dihedral = atoms.get_dihedral(i, j, k, l, mic=False)
                    if dihedral > 180:
                        dihedral -= 360
                    elif dihedral < -180:
                        dihedral += 360
                    dihedral_angles.append(dihedral)
                    seen_dihedrals.add(idx_seq)

    # === step C: average ===
    avg_length = np.mean(bond_lengths) if bond_lengths else 0.0
    avg_angle = np.mean(bond_angles) if bond_angles else 0.0
    avg_dihedral = np.mean(np.abs(dihedral_angles)) if dihedral_angles else 0.0

    return {
        "bonds": avg_length,
        "angles": avg_angle,
        "DA": avg_dihedral
    }

def get_HOMO_LUMO(text):
    dict_hl ={'HOMO-LUMO':None}

    pattern1 = r'occ orbital:(.*?)vir orbital'
    pattern2 = r'vir orbital:(.*?)(?=[A-Za-z])'
    occ_orbi = re.findall(pattern1, text, re.S)[-1].strip().splitlines()
    vir_orbi = re.findall(pattern2, text, re.S)[-1].strip().splitlines()
    HOMO = float(occ_orbi[-1].strip().split()[-1]) * 27.2113814998
    LUMO = float(vir_orbi[1].strip().split()[0]) * 27.2113814998
    dict_hl['HOMO-LUMO'] = LUMO - HOMO


    return dict_hl

def get_rotational_constant(text):
    rc={'rotational_constant':{'A':None, 'B':None,'C':None,},'rays_asymmetry_parameter':None}
    pattern = r'Rotational Constants \[GHZ\]:(.*?)(?=[A-Za-z])'
    B_abc = re.findall(pattern, text, re.S)[-1].strip().split()
    rc['rotational_constant']['A'] = float(B_abc[-1])
    rc['rotational_constant']['B'] = float(B_abc[1])
    rc['rotational_constant']['C'] = float(B_abc[0])

    rc['rays_asymmetry_parameter'] = (2*float(B_abc[1])-float(B_abc[-1])-float(B_abc[0]))/(float(B_abc[-1])-float(B_abc[0]))

    return rc
    

def get_excited_energy(text):
    """
    get excited energy
    output: list of excited energy
    The list index corresponds to the excited state order.

    """
    pattern = r'={2,}\s*Excitation energies and oscillator strengths\s*={2,}([\s\S]*?)={2,}'
    pattern_e = r'E\s*=\s*(\d+\.\d+)\s*eV'
    excited_array = re.findall(pattern, text, re.MULTILINE | re.DOTALL)
    excited_energy = []
    for state in excited_array:
        excited_energy.append(re.findall(pattern_e, state, re.S))

    return excited_energy[-1]

def get_charge(text):
    """
    get Mulliken charge from text
    output: element list and charge list
    the index of list  corresponds to the atoms order in aop file
    
    """ 
    pattern = r'Mulliken charges:\s*\n([\s\S]*?)\n\s*Sum of Mulliken charges\s*=\s*[\d\.\-]+'
    charge_line = re.findall(pattern, text, re.S)
    element = []
    charge = []
    for line in charge_line[-1].splitlines():
        line = line.split()
        element.append(line[1])
        charge.append(float(line[-1]))
    return element, charge

def get_features_dict(aop, run_type, log, args, *features_name):
    """
    get various features from amesp output aop file
    output: dict of features
    now avilable features:

    - 'charge'
    - 'excited_energy'
    - 'structure'
    - 'HOMO-LUMO'

    """
    features = {}
    
    # 基本输入检查
    if not features_name:
        return features  # 如果没有指定特征，返回空字典

    for func in features_name:
        if func == 'HOMO-LUMO':
            if get_HOMO_LUMO(aop):
                features.update(get_HOMO_LUMO(aop))
            else:
                log.info("HOMO-LUMO not found")
        elif func == 'charge':
            features[func] = {}
            if get_charge(aop):
                features[func]['element'], features[func]['charge'] = get_charge(aop)
            else:
                log.info("Charge not found")

        elif func == 'excited_energy':
            if run_type == 'excit':
                if get_excited_energy(aop):
                    features[func] = get_excited_energy(aop)[0]
                else:
                    log.info("Excited energy not found")
            else:
                continue
        elif func == 'structure':
            if get_structure_prop(aop):
                log.info(f"Structure found from aop for {run_type}")
                features[func] = get_structure_prop(aop)
            else:
                log.info(F"Structural features calculated from ASE for {run_type}  ")
                features[func] = calculate_structure_prop(args,run_type)

        elif func == 'rotational_constant':
            if get_rotational_constant(aop):
                features.update(get_rotational_constant(aop))
            else:
                log.info("Rational constant not found")
        else:
            ValueError(f"Feature {func} not recognized.")
    
    return features

if __name__ == '__main__':
    ''' 
    update result.json and features.json with structure features in dirctory 'cache'
    
    You can chose to update only structure or other properties for results.json, and features.json will also updated based on new resultss.json.

    '''
    import ase.io as io
    from ase import Atoms
    import argparse
    import logging,json
    from pathlib import Path
    from typing import Dict, Any, Optional, Tuple

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    log = logging.getLogger(__name__)

    def parse_args():
        p = argparse.ArgumentParser(description="CI-NEB + volume by Multiwfn (Folder Isolated)")
        p.add_argument("--workdir", default="work_dirs", help="working directory")
        p.add_argument("--properties", default="HOMO-LUMO,charge,structure,rotational_constant,excited_energy", help="properties to extract, comma-separated")
        args, f = p.parse_known_args()
        return args
    
    def extract_features(result: Dict[str, Any]):
        """
        Extract structured features from valid result.json.

        Maps AIE-aTB output to our features.json schema:
        - s0_volume, s1_volume, delta_volume
        - s0_homo_lumo_gap, s1_homo_lumo_gap, delta_gap
        - s0_dihedral_avg, s1_dihedral_avg, delta_dihedral
        - s0_charge_dipole, s1_charge_dipole, delta_dipole (computed if possible)
        - excitation_energy (null in V0)
        - neb_mean_volume

        Args:
            result: Parsed result.json dict

        Returns:
            features dict matching our schema
        """
        gs = result["ground_state"]
        es = result["excited_state"]
        neb = result.get("exciting_path_mean_volume")

        # Volume
        s0_volume = gs.get("volume")
        s1_volume = es.get("volume")
        delta_volume = (s1_volume - s0_volume) if (s0_volume is not None and s1_volume is not None) else None

        # HOMO-LUMO gap (stored as string in result.json)
        s0_gap_str = gs.get("HOMO-LUMO")
        s1_gap_str = es.get("HOMO-LUMO")
        s0_homo_lumo_gap = float(s0_gap_str) if s0_gap_str else None
        s1_homo_lumo_gap = float(s1_gap_str) if s1_gap_str else None
        delta_gap = (s1_homo_lumo_gap - s0_homo_lumo_gap) if (s0_homo_lumo_gap is not None and s1_homo_lumo_gap is not None) else None

        # Dihedral average (from structure.DA)
        s0_struct = gs.get("structure", {})
        s1_struct = es.get("structure", {})
        s0_dihedral_avg = s0_struct.get("DA")
        s1_dihedral_avg = s1_struct.get("DA")
        delta_dihedral = (s1_dihedral_avg - s0_dihedral_avg) if (s0_dihedral_avg is not None and s1_dihedral_avg is not None) else None

        # Charge dipole - compute from Mulliken charges if available
        delta_dipole = result.get("charge")
        # Additional structure properties (for reference)
        s0_bonds_avg = s0_struct.get("bonds")
        s1_bonds_avg = s1_struct.get("bonds")
        delta_bonds = s1_bonds_avg - s0_bonds_avg if s0_bonds_avg is not None and s1_bonds_avg is not None else None
        s0_angles_avg = s0_struct.get("angles")
        s1_angles_avg = s1_struct.get("angles")
        delta_angles = s1_angles_avg - s0_angles_avg if s0_angles_avg is not None and s1_angles_avg is not None else None

        # rotational_constant
        s0_rc_a = gs['rotational_constant'].get('A')
        s0_rc_b = gs['rotational_constant'].get('B')
        s0_rc_c = gs['rotational_constant'].get('C')
        s1_rc_a = es['rotational_constant'].get('A')
        s1_rc_b = es['rotational_constant'].get('B')
        s1_rc_c = es['rotational_constant'].get('C')
        s0_rap = gs['rays_asymmetry_parameter']
        s1_rap = es['rays_asymmetry_parameter']


        features = {
            # rotational_constant
            "s0_rotational_constant_a" : s0_rc_a,
            "s0_rotational_constant_b" : s0_rc_b,
            "s0_rotational_constant_c" : s0_rc_c,
            "s1_rotational_constant_a" : s1_rc_a,
            "s1_rotational_constant_b" : s1_rc_b,
            "s1_rotational_constant_c" : s1_rc_c,
            "s0_rays_asymmetry_parameter" : s0_rap,
            "s1_rays_asymmetry_parameter" : s1_rap,
            # Volume
            "s0_volume": s0_volume,
            "s1_volume": s1_volume,
            "delta_volume": delta_volume,
            # HOMO-LUMO gap
            "s0_homo_lumo_gap": s0_homo_lumo_gap,
            "s1_homo_lumo_gap": s1_homo_lumo_gap,
            "delta_gap": delta_gap,
            # Dihedral
            "s0_dihedral_avg": s0_dihedral_avg,
            "s1_dihedral_avg": s1_dihedral_avg,
            "delta_dihedral": delta_dihedral,
            # Charge dipole (computed from Mulliken charges)
            "delta_dipole": delta_dipole,
            # Excitation energy - not directly in result.json, set null for V0
            "excitation_energy": es.get('excited_energy'),
            # NEB mean volume
            "exciting_path_mean_volume": neb,
            # Extra structure metrics (informational)
            "s0_bonds_avg": s0_bonds_avg,
            "s1_bonds_avg": s1_bonds_avg,
            "delta_bonds" : delta_bonds,
            "s0_angles_avg": s0_angles_avg,
            "s1_angles_avg": s1_angles_avg,
            "delta_angles" : delta_angles

        }

        return features
    
    args = parse_args()

    target_dir = Path('cache')

    status_files = list(target_dir.rglob('status.json'))
    n_failed =0
    n_replaced =0
    idx=1
    
    for json_file in status_files:
        try:
            # 读取status json文件
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查run_status字段是否存在
            run_status = data.get('run_status')
            log.info(f"[{idx}/{len(status_files)}] Processing.")
            
            if run_status == 'success':
                workdir = json_file.parent
                args.workdir = str(workdir)
                
                log.info(f"Processing {workdir}...")
                
                # 获取结构特征
                S0_aop = open(f"{workdir}/opt/opt_run.aop", 'r', encoding='utf-8').read()
                S1_aop = open(f"{workdir}/excit/excit_run.aop", 'r', encoding='utf-8').read()

                structure_S0 = get_features_dict(S0_aop, 'opt', log, args, *["structure"])
                structure_S1 = get_features_dict(S1_aop, 'excit', log, args, *["structure"])

                log.info(f"Finished calculating structure features for {workdir}")
            
                # 查找并读取result.json文件
                result_files = list(workdir.rglob('result.json'))
                if not result_files:
                    log.error(f"在目录 {workdir} 中未找到result.json文件")
                    continue
                    
                result_file = result_files[0]
                with open(result_file, 'r', encoding='utf-8') as f:
                    results = json.load(f)
                
                # 更新ground_state结构
                log.info(f"Update Structure of S0")
                results['ground_state']['structure'] = structure_S0['structure']

                # 更新excited_state结构
                log.info(f"Update Structure of S1:")
                results['excited_state']['structure'] = structure_S1['structure']


                # 写回result.json文件
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)

                log.info(f"Updated result.json for {workdir}")

                # 处理features.json文件
                features_files = list(workdir.rglob('features.json'))

                features_file = features_files[0]
                with open(features_file, 'r', encoding='utf-8') as f:
                    existing_features = json.load(f)

                log.info("Extracting updated features...")
                features = extract_features(results)
                
                # 更新现有特征（保留原有特征）
                existing_features.update(features)
                
                with open(features_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_features, f, indent=2, ensure_ascii=False)

                log.info(f"Updated features.json for {workdir}")  
                n_replaced +=1
            else:
                n_failed +=1
                log.error(f"Skipt it, because aTB caculation is not successful. run_status: {run_status}")  

        except Exception as e:
            log.error(f"错误: 处理文件 {json_file} 时发生异常 - {str(e)}")
            import traceback
            log.error(traceback.format_exc())
        idx+=1
    log.info(f"Total successful updates: {n_replaced}, Total skipped due to failure: {n_failed}")