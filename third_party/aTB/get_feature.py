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
    pattern = r'HOMO-LUMO gap:.*?=\s*(\d+\.\d+)\s*eV'
    HUMO_LUMO = re.findall(pattern, text, re.S)

    return HUMO_LUMO[-1]

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
    return element, charge

def get_features_dict(xyz, type, log,*args):
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
                features[func] = get_HOMO_LUMO(xyz)
            else:
                log.info("HOMO-LUMO not found")
        elif func == 'charge':
            features[func] = {}
            if get_charge(xyz):
                features[func]['element'], features[func]['charge'] = get_charge(xyz)
            else:
                log.info("Charge not found")
        elif func == 'excited_energy':
            if type == 'excit':
                if get_excited_energy(xyz):
                    features[func] = get_excited_energy(xyz)
                else:
                    log.info("Excited energy not found")
            else:
                continue
        elif func == 'structure':
            if get_structure_prop(xyz):
                features[func] = get_structure_prop(xyz)
            else:
                log.info("Structure not found")
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