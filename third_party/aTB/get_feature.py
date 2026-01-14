import re
import numpy as np

def get_structure_prop(text):
    bonds = [] #Bohr
    angel = [] #Radian
    DA = []  #Radian
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
        'DA': np.mean(DA) * (180.0 / np.pi)  # convert Radian to Degree
                    }
    return av_structure

def get_HOMO_LUMO(text):
<<<<<<< HEAD
    pattern = r'HOMO-LUMO gap:.*?=\s*(\d+\.\d+)\s*eV'
    HUMO_LUMO = re.findall(pattern, text, re.S)

    return HUMO_LUMO[-1]
=======
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
    print(re.findall(pattern, text, re.S))
    B_abc = re.findall(pattern, text, re.S)[-1].strip().split()
    rc['rotational_constant']['A'] = float(B_abc[-1])
    rc['rotational_constant']['B'] = float(B_abc[1])
    rc['rotational_constant']['C'] = float(B_abc[0])

    rc['rays_asymmetry_parameter'] = (2*float(B_abc[1])-float(B_abc[-1])-float(B_abc[0]))/(float(B_abc[-1])-float(B_abc[0]))

    return rc
    
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)

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
    the index of list  corresponds to the atoms order in xyz file
    
    """ 
    pattern = r'Mulliken charges:\s*\n([\s\S]*?)\n\s*Sum of Mulliken charges\s*=\s*[\d\.\-]+'
    charge_line = re.findall(pattern, text, re.S)
    element = []
    charge = []
    for line in charge_line[-1].splitlines():
        line = line.split()
        element.append(line[1])
        charge.append(float(line[-1]))
<<<<<<< HEAD
    return element, charge

def get_features_dict(xyz, type, log,*args):
=======
    return element, np.array(charge)

def get_features_dict(xyz, run_type, log,*args):
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
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
    if not args:
        return features  # 如果没有指定特征，返回空字典
    
    for func in args:
        if func == 'HOMO-LUMO':
            if get_HOMO_LUMO(xyz):
<<<<<<< HEAD
                features[func] = get_HOMO_LUMO(xyz)
=======
                features.update(get_HOMO_LUMO(xyz))
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
            else:
                log.info("HOMO-LUMO not found")
        elif func == 'charge':
            features[func] = {}
            if get_charge(xyz):
                features[func]['element'], features[func]['charge'] = get_charge(xyz)
            else:
                log.info("Charge not found")
        elif func == 'excited_energy':
<<<<<<< HEAD
            if type == 'excit':
                if get_excited_energy(xyz):
                    features[func] = get_excited_energy(xyz)
=======
            print(run_type)
            if run_type == 'excit':
                print(run_type)
                if get_excited_energy(xyz):
                    features[func] = get_excited_energy(xyz)[0]
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
                else:
                    log.info("Excited energy not found")
            else:
                continue
        elif func == 'structure':
            if get_structure_prop(xyz):
                features[func] = get_structure_prop(xyz)
            else:
                log.info("Structure not found")
<<<<<<< HEAD
=======
        elif func == 'rotational_constant':
            if get_rotational_constant(xyz):
                features.update(get_rotational_constant(xyz))
            else:
                log.info("Rational constant not found")
>>>>>>> 605e931 (add ionic caculator & rota. const. & excited energy)
        else:
            # 不支持的特征类型
            features[func] = f"错误: 不支持的特征 '{func}'"
    
    return features

if __name__ == '__main__':
    with open('work_dirs/excited/excited_run.aop') as f:
        text = f.read()
        # y = get_HOMO_LUMO(text)
        # y = get_excited_energy(text)
        # y = get_structure_prop(text)
        # element, charge = get_charge(text)
        features = get_features_dict(text, *['HOMO-LUMO', 'charge', 'excited_energy', 'structure'])
        print(features)