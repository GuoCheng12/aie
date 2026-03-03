
import torch
import torch_geometric
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds


def mol_to_graph(xyz_path, charges,type_cal,charge_mol,args,log=None):
    """
    手动将 RDKit Mol 对象转换为 PyG Data 对象。
    参数：
        mol (rdkit.Chem.Mol): RDKit 分子对象。
    """
    try:
        raw_mol = Chem.MolFromXYZFile(xyz_path)
        raw_mol.UpdatePropertyCache(strict=False)
        
        mol = Chem.Mol(raw_mol)
        mol.UpdatePropertyCache(strict=False)

        rdDetermineBonds.DetermineBonds(mol, useHueckel=False, charge=charge_mol, embedChiral=False)

        # 1. 提取原子（节点）特征
        atom_features_list = []
        for atom in mol.GetAtoms():
            # 根据你的需求组合特征，例如：
            feature = [
                atom.GetAtomicNum(),        # 原子序数
                atom.GetDegree(),           # 度数（连接的原子数）
                atom.GetFormalCharge(),     # 形式电荷
                int(atom.GetIsAromatic()),  # 是否属于芳香环
                charges[atom.GetIdx()]      # 预计算的电荷值
            ]
            atom_features_list.append(feature)
        x = torch.tensor(atom_features_list, dtype=torch.float)

        edge_indices = []  # 边的连接关系
        edge_features_list = []  # 边的特征
        
        for bond in mol.GetBonds():
            # 获取相连原子的索引
            i = bond.GetBeginAtomIdx()
            j = bond.GetEndAtomIdx()
            
            # 添加双向的边（因为分子图通常是无向图）
            edge_indices.append([i, j])
            edge_indices.append([j, i])
            
            # 提取键的特征，例如键类型和是否共轭
            bond_feature = [
                int(bond.GetBondType()),    # 键类型
                int(bond.GetIsConjugated()), # 是否共轭
                int(bond.IsInRing()),       # 是否在环上
            ]
            # 每条边对应同样的特征
            edge_features_list.append(bond_feature)
            edge_features_list.append(bond_feature)
        
        if len(edge_indices) == 0:
            # 处理无键的情况（如单原子）
            edge_index = torch.empty((2, 0), dtype=torch.long)
            edge_attr = torch.empty((0, 3), dtype=torch.float)  # 特征维度与bond_feature长度一致
        else:
            edge_index = torch.tensor(edge_indices, dtype=torch.long).t().contiguous()
            edge_attr = torch.tensor(edge_features_list, dtype=torch.float)
        
        # 3. 构建 PyG Data 对象
        data = torch_geometric.data.Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
        torch.save(data, f'{args.workdir}/{type_cal}_graph.pt')
        log.info(f"Graph saved to {args.workdir}/{type_cal}_graph.pt")
    except Exception as e:
        log.info(f"Failed to save graph: {e}")

    return data, mol


# 主程序
if __name__ == "__main__":
    xyz_path = './sample/opt/opted.xyz'
    
    # 转换分子为图数据
    graph_data, mol = mol_to_graph_data_obj(xyz_path, charges)
    print(f"图数据节点特征形状: {graph_data.x.shape}")
    
    # 保存图数据
    torch.save(graph_data, 'molecule_graph.pt')
    print("图数据已保存为 'molecule_graph.pt'")
    
    # 1. 打印图摘要信息
    print_graph_summary(graph_data, mol)
    
    # 2. 使用NetworkX可视化图结构
    visualize_graph_networkx(graph_data, mol, 'networkx_graph.png')
    
    # 4. 可视化节点特征矩阵
    visualize_node_features(graph_data, 'node_features.png')
    
    print("\n所有可视化已完成！")


