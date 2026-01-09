#!/usr/bin/env python
# coding: utf-8

# In[1]:
import argparse, logging,calculator,json
from ase import io
# In[ ]:
# -------------------- input parameters --------------------
def parse_args():
    p = argparse.ArgumentParser(description="CI-NEB + volume by Multiwfn (Folder Isolated)")
    p.add_argument("--begin", default="begin.xyz", help="initial structure file path")
    p.add_argument("--end",  default="end.xyz",  help="excited structure file path")
    p.add_argument("--nimg",  type=int, default=3, help="number of images for NEB calculation")
    p.add_argument("--neb_fmax",  type=float, default=0.1, help="max force convergence threshold for NEB (eV/Å-1)")
    p.add_argument("--opt_fmax",  type=float, default=0.03, help="max force convergence threshold for opt (eV/Å-1)")
    p.add_argument("--npara", type=int, default=2, help="number of parallel processes for Amesp")
    p.add_argument("--maxcore", type=int, default=4000, help="avilable memory (in MB) for Amesp")
    p.add_argument("--workdir", default="work_dirs", help="working directory")
    p.add_argument("--properties", default="HOMO-LUMO,charge,structure", help="properties to extract, comma-separated")
    p.add_argument("--smiles",default=None,help="SMILES string")
    p.add_argument("--charge",type=int,default=None,help="Molecular charge (auto-detected from SMILES if not provided)")
    p.add_argument("--nstates",default=3,help="Number of excited states")
    p.add_argument("--excit_root",default=1,help="number of the excited state focused on")
    p.add_argument("--mult",default=1,help="multiplicity of the excited state")
    args, f = p.parse_known_args()
    return args


# In[3]:


def analysis(args,type,log):
    import get_feature
    dirs = f'{args.workdir}/{type}/{type}'
    log.info(f"Analyzing {type} calculation")
    features = {}
    features = get_feature.get_features_dict(open(dirs+'_run.aop').read(), type, log,*args.properties.split(','))   
    return features


# In[4]:
def get_formal_charge_from_smiles(smiles):
    """
    Calculate formal charge from SMILES string.

    Parameters:
        smiles: SMILES string

    Returns:
        int: Total formal charge of the molecule
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0  # Default to neutral if SMILES is invalid

    return Chem.GetFormalCharge(mol)


def smiles_to_ase_atoms(smiles, random_seed=42):
    """
    Convert SMILES string to ASE Atoms object (with robust 3D embedding).

    Parameters:
        smiles: SMILES string
        random_seed: Random seed for reproducible initial structure generation

    Returns:
        ASE Atoms object
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from ase import Atoms

    # 1. Create RDKit molecule object from SMILES
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles}")

    # 2. Add hydrogen atoms (required for 3D structure generation)
    mol = Chem.AddHs(mol)

    # 3. Generate initial 3D coordinates with ETKDG (fallback to random coords)
    params = None
    if hasattr(AllChem, "ETKDGv3"):
        params = AllChem.ETKDGv3()
    elif hasattr(AllChem, "ETKDGv2"):
        params = AllChem.ETKDGv2()
    elif hasattr(AllChem, "ETKDG"):
        params = AllChem.ETKDG()

    if params is not None:
        params.randomSeed = random_seed
        if hasattr(params, "maxAttempts"):
            params.maxAttempts = 200
        res = AllChem.EmbedMolecule(mol, params)
        if res != 0 and hasattr(params, "useRandomCoords"):
            params.useRandomCoords = True
            res = AllChem.EmbedMolecule(mol, params)
    else:
        res = AllChem.EmbedMolecule(mol, randomSeed=random_seed)
        if res != 0:
            res = AllChem.EmbedMolecule(mol, randomSeed=random_seed, useRandomCoords=True)

    if res != 0 or mol.GetNumConformers() == 0:
        raise ValueError("RDKit embedding failed (no conformer generated)")

    # 4. Light geometry cleanup (does not replace quantum optimization)
    try:
        AllChem.UFFOptimizeMolecule(mol, maxIters=200)
    except Exception:
        pass

    # 5. Get the conformation (3D coordinates)
    conf = mol.GetConformer()

    # 6. Extract atom information and create ASE Atoms object
    positions = []
    symbols = []

    for atom in mol.GetAtoms():
        # Get atom coordinates
        pos = conf.GetAtomPosition(atom.GetIdx())
        positions.append([pos.x, pos.y, pos.z])

        # Get element symbol
        symbols.append(atom.GetSymbol())

    # 7. Create ASE Atoms object
    atoms = Atoms(symbols=symbols, positions=positions)

    return atoms

# In[5]:
# -------------------- main function -------------------
def main():
    # log setting
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    log = logging.getLogger(__name__)

    args = parse_args()

    # read in the initial and final structures
    if args.smiles:
        log.info("Start generating begin structure from SMILES")
        initial = smiles_to_ase_atoms(args.smiles)

        # Auto-detect charge from SMILES if not provided
        if args.charge is None:
            args.charge = get_formal_charge_from_smiles(args.smiles)
            log.info(f"Auto-detected charge from SMILES: {args.charge}")
        else:
            log.info(f"Using provided charge: {args.charge}")
    else:
        log.info("Start reading begin structure from xyz file")
        initial = io.read(args.begin)
        if args.charge is None:
            args.charge = 0  # Default to neutral for xyz input
            log.info(f"Using default charge: {args.charge}")


    # 1. optimize initial structure
    opted_atoms=calculator.run_calculate(args, 'opt', initial,log)
    opted_features = analysis(args,'opt',log)
    opted_features['volume'] = calculator.volume_Mutifwfn(f'{args.workdir}/opt/opted.xyz') 

    # 2. optimize excited structure
    excited_atoms = calculator.run_calculate(args, 'excit', opted_atoms,log)
    excited_features = analysis(args,'excit',log)
    excited_features['volume'] = calculator.volume_Mutifwfn(f'{args.workdir}/excit/excited.xyz') 

    # 3. run NEB calculation to simulate the excitation process
    neb_imgs = calculator.run_calculate(args, 'neb', opted_atoms,log, excited_atoms)

    # 4. calculate the volume
    volumes = calculator.compute_all_volumes(args,neb_imgs,log)
    neb_mean_volume=(sum(volumes)/len(volumes))

    log.info(f"NEB mean volume: {neb_mean_volume}")

    results = {'ground_state' : opted_features, 'excited_state' : excited_features,'NEB': neb_mean_volume}

    with open(args.workdir+'/result.json', 'w') as f:
        json.dump(results, f)

# In[6]:
if __name__ == "__main__":
    main()
