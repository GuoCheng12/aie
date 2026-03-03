import json
from pathlib import Path
from collections import Counter




def count_run_status(directory_path):
    """
    统计指定目录下所有 status.json 文件中的 'run_status' 字段值
    
    Args:
        directory_path (str): 要搜索的根目录路径，例如 'cache'
        
    Returns:
        Counter: 包含每个 run_status 值及其出现次数的计数器对象
    """
    status_counter = Counter()
    target_dir = Path(directory_path)
    
    # 递归查找所有 status.json 文件
    json_files = target_dir.rglob('status.json')
    print(f"Found {len(json_files)} status.json files in {directory_path}")
    
    for json_file in json_files:
        try:
            # 读取并解析JSON文件
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查 run_status 字段是否存在
            run_status = data.get('run_status')
            if run_status is not None:
                status_counter[run_status] += 1
            else:
                print(f"警告: 文件 {json_file} 中未找到 'run_status' 字段")
                
        except json.JSONDecodeError:
            print(f"错误: 文件 {json_file} 不是有效的JSON格式")
        except Exception as e:
            print(f"错误: 读取文件 {json_file} 时发生异常 - {str(e)}")
    
    return status_counter

import os
import pandas as pd
import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import PatternFill

import os
import json
import pandas as pd
import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import PatternFill
from pathlib import Path

def load_status_files(cache_dir="cache"):
    """递归查找cache目录下所有status.json文件并加载数据[1,3](@ref)"""
    json_data = []
    
    # 使用os.walk递归遍历所有子目录[1,3](@ref)
    for root, dirs, files in os.walk(cache_dir):
        if "status.json" in files:
            filepath = os.path.join(root, "status.json")
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 提取run_status（支持嵌套结构）
                run_status = data.get('run_status', 'unknown')
                if isinstance(run_status, dict):
                    run_status = run_status.get('status', 'unknown')
                
                # 提取inchikey
                inchikey = data.get('inchikey', '')
                
                json_data.append({
                    'run_status': run_status,
                    'inchikey': inchikey,
                    'all_data': data
                })
                print(f"已加载: {filepath}")
                
            except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
                print(f"错误处理文件 {filepath}: {str(e)}")
                continue
    
    return json_data

def query_parquet_ids(inchikey_list, parquet_path="data/molecule_table.parquet"):
    """根据incheikey列表从Parquet文件中查询匹配的ID[12](@ref)"""
    try:
        # 读取Parquet文件（使用列投影优化性能[12](@ref)）
        table = pq.read_table(parquet_path, columns=['inchikey', 'id_list'])
        df = table.to_pandas()
        
        # 构建查询结果字典
        id_mapping = {}
        for inchikey in inchikey_list:
            id_data={}
            series = df[df['inchikey']==inchikey]['id_list']
            matched_ids = series.tolist()
            id_list = [int(id) for id in matched_ids[0]]
            matched_ids = ','.join(map(str,id_list))
            id_data.update({'id':matched_ids,'inchikey':inchikey})

            raw_data = query_expirment_ids(id_list)
            if raw_data :
                id_data.update(raw_data)
            else :
                id_data.update({'read': 'raw data is empty'})
            
            id_mapping[inchikey] = id_data

        return id_mapping
    
    except Exception as e:
        print(f"Parquet查询失败: {str(e)}")
        return {'read': 'There is no molecule in parquet file.'}

def query_expirment_ids(id_list, data_path="rag_compound_new.csv"):

    try:

        df = pd.read_csv(data_path)
        df_raw = pd.read_csv("ASBase260202.csv",encoding='gbk')
        
        # 构建查询结果字典
        id_mapping = {}
        existing_ids = df_raw['id'].isin(id_list)
        if_AIE = existing_ids.any()
        
        if if_AIE:
            if len(id_list) == 1:
                df_id = df[df['id'] == id_list[0]]

            else:
                df_id = pd.DataFrame()
                for id in id_list:
                    if df_id.empty:
                        df_id = df[df['id'] == id]

            if df_id.empty:
                return {'read': "AIE, but no doi"}

            id_mapping = df_id.drop('id',axis=1).astype(str)
            id_mapping = id_mapping.to_dict(orient='records')
            return id_mapping[0]
        else:
            return {'read': "No AIE"}
        
    except Exception as e:
        print(f"CSV查询失败: {str(e)}")
        return {'read': "Can't search from rag_compound_new.csv"}

def export_to_excel(json_data, id_mapping, output_file="status_report.xlsx"):
    """将JSON数据和查询结果导出到Excel[9,10](@ref)"""
    # 准备Excel数据
    excel_data = []
    for item in json_data:
        flat_data = {
            'run_status': item['run_status'],
            'inchikey': item['inchikey'],  # 列表转字符串便于显示
        }
        flat_data.update(id_mapping[item['inchikey']])
        # 扁平化嵌套JSON数据（简化示例，实际可递归处理[8](@ref)）
        if isinstance(item['all_data'], dict):
            for key, value in item['all_data'].items():
                if key not in ['run_status', 'inchikey']:  # 避免重复字段
                    if isinstance(value, (list, dict)):
                        flat_data[key] = str(value)  # 复杂结构转字符串
                    else:
                        flat_data[key] = value
        excel_data.append(flat_data)
    
    # 创建DataFrame并导出[10](@ref)
    df = pd.DataFrame(excel_data)
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Status报告', index=False)

    print(f"结果已导出到: {output_file}")



if __name__ == "__main__":
    # 1. 读取所有status.json文件[3,4](@ref)
    cache_dir = "/mnt/afs/250010045/5-AIE-code/cache/atb"  # 可修改路径
    # cache_dir = "/mnt/afs/250010045/5-AIE-code/cache/atb/AA/"  # 可修改路径
    parquet_path = "data/molecule_table.parquet"
    k=0
    
    print("正在扫描JSON文件...")
    json_data = load_status_files(cache_dir)

    if not json_data:
        print("未找到有效的status.json文件")
        exit()
    
    # 2. 提取incheikey列表并查询Parquet[12](@ref)
    inchikey_list = [item['inchikey'] for item in json_data if item['inchikey']]
    print(f"正在查询{len(inchikey_list)}个inchikey...")
    id_mapping = query_parquet_ids(inchikey_list, parquet_path)
    print(k)
    
    # 3. 导出结果到Excel
    export_to_excel(json_data, id_mapping, "status_report.xlsx")
    
    # 输出统计信息
    failed_count = sum(1 for item in json_data if item['run_status'] == 'failed')
    print(f"处理完成！共处理{len(json_data)}个文件，其中{failed_count}个状态为failed。")

    