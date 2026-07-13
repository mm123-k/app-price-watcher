"""
価格履歴をJSONファイルとして読み書きする共通モジュール。

GitHub Actionsはジョブごとに使い捨ての実行環境なので、状態(=これまでの最安値)を
リポジトリ内のJSONファイルに保存し、ワークフローの最後にgit commitして永続化する。
"""
import json
import os
from typing import Any, Dict


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
