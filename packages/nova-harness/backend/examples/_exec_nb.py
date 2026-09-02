"""Notebook 忠实执行器（examples 校验工具）。

用 jupyter_client 起真实内核逐 cell 执行并打印输出/错误——
修改 examples 下的 notebook 后用它验证：
``python _exec_nb.py 02-sdk-tour.ipynb``
"""

import json
import sys

from jupyter_client.manager import KernelManager

nb_path = sys.argv[1]
nb = json.load(open(nb_path))

km = KernelManager(kernel_name="python3")
km.start_kernel()
kc = km.client()
kc.start_channels()
kc.wait_for_ready(timeout=60)

failed = False
for i, cell in enumerate(nb["cells"]):
    if cell["cell_type"] != "code":
        continue
    print(f"\n===== cell {i} =====", flush=True)
    src = cell["source"]
    if isinstance(src, list):  # Jupyter 保存的多行格式 → 拼回单串
        src = "".join(src)
    msg_id = kc.execute(src)
    while True:
        try:
            msg = kc.get_iopub_msg(timeout=300)
        except Exception as e:
            print("TIMEOUT waiting kernel:", e)
            failed = True
            break
        if msg["parent_header"].get("msg_id") != msg_id:
            continue
        mtype = msg["msg_type"]
        content = msg["content"]
        if mtype == "stream":
            print(content["text"], end="", flush=True)
        elif mtype == "execute_result":
            print(content["data"].get("text/plain", ""))
        elif mtype == "error":
            print("ERROR:", content["ename"], content["evalue"])
            for line in content.get("traceback", [])[-6:]:
                print(" ", line)
            failed = True
        elif mtype == "status" and content["execution_state"] == "idle":
            break
    if failed:
        break

kc.stop_channels()
km.shutdown_kernel()
print("\n===== RESULT:", "FAILED" if failed else "OK", "=====")
sys.exit(1 if failed else 0)
