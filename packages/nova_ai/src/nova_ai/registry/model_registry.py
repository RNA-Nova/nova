"""
模型注册表
管理模型注册和查询
"""

from typing import Dict, Optional, List
from ..types.model import Model


class ModelRegistry:
    """模型注册表"""

    def __init__(self):
        self._models: Dict[str, Dict[str, Model]] = {}

    def register_model(self, provider: str, model: Model) -> None:
        """
        注册单个模型

        Args:
            provider: 提供商名称
            model: 模型对象
        """
        self._models.setdefault(provider, {})[model.id] = model

    def register_models(self, provider: str, models: Dict[str, Model]) -> None:
        """
        注册多个模型

        Args:
            provider: 提供商名称
            models: 模型字典
        """
        self._models.setdefault(provider, {}).update(models)

    def get_model(self, provider: str, model_id: str) -> Optional[Model]:
        """
        通过提供商和模型ID获取模型（双key查找）

        Args:
            provider: 提供商名称
            model_id: 模型ID

        Returns:
            找到的模型，如果不存在则返回None
        """
        if provider in self._models:
            return self._models[provider].get(model_id)
        return None

    def get_models_by_provider(self, provider: str) -> Dict[str, Model]:
        """获取指定提供商的所有模型"""
        if provider in self._models:
            return self._models[provider].copy()
        return {}

    def list_providers(self) -> List[str]:
        """列出所有提供商"""
        return list(self._models.keys())

    def list_all_models(self) -> Dict[str, Dict[str, Model]]:
        """
        列出所有模型，按提供商分组

        Returns:
            按提供商分组的模型字典
        """
        return {name: models.copy() for name, models in self._models.items()}

    def list_all_models_flat(self) -> Dict[str, Model]:
        """
        列出所有模型，扁平化返回
        注意：如果多个提供商有相同ID的模型，后面的会覆盖前面的

        Returns:
            模型ID到模型的映射字典
        """
        result = {}
        for models in self._models.values():
            result.update(models)
        return result

    def get_model_by_id(self, model_id: str) -> Optional[Model]:
        """通过ID在所有提供商中查找模型"""
        for models in self._models.values():
            if model_id in models:
                return models[model_id]
        return None

    def find_model(self, model_id: str) -> Optional[Model]:
        """通过ID查找模型（同get_model_by_id）"""
        return self.get_model_by_id(model_id)

    def remove_model(self, provider: str, model_id: str) -> bool:
        """
        移除指定模型

        Args:
            provider: 提供商名称
            model_id: 模型ID

        Returns:
            是否成功移除
        """
        if provider in self._models:
            if model_id in self._models[provider]:
                del self._models[provider][model_id]
                return True
        return False

    def remove_provider(self, provider: str) -> bool:
        """
        移除指定提供商及其所有模型

        Args:
            provider: 提供商名称

        Returns:
            是否成功移除
        """
        if provider in self._models:
            del self._models[provider]
            return True
        return False

    def clear(self) -> None:
        """清空注册表"""
        self._models.clear()


# 全局注册表实例
_model_registry = ModelRegistry()


def register_model(provider: str, model: Model) -> None:
    """注册模型"""
    _model_registry.register_model(provider, model)


def register_models_from_dict(provider: str, models: Dict[str, Model]) -> None:
    """从字典注册模型"""
    _model_registry.register_models(provider, models)


def get_model(provider: str, model_id: str) -> Optional[Model]:
    """通过提供商和模型ID获取模型"""
    return _model_registry.get_model(provider, model_id)


def get_model_by_id(model_id: str) -> Optional[Model]:
    """通过ID在所有提供商中查找模型"""
    return _model_registry.get_model_by_id(model_id)


def get_models_by_provider(provider: str) -> Dict[str, Model]:
    """获取指定提供商的所有模型"""
    return _model_registry.get_models_by_provider(provider)


def list_providers() -> List[str]:
    """列出所有提供商"""
    return _model_registry.list_providers()


def list_all_models() -> Dict[str, Dict[str, Model]]:
    """列出所有模型（按提供商分组）"""
    return _model_registry.list_all_models()


def list_all_models_flat() -> Dict[str, Model]:
    """列出所有模型（扁平化）"""
    return _model_registry.list_all_models_flat()


def find_model_by_id(model_id: str) -> Optional[Model]:
    """通过ID查找模型"""
    return _model_registry.find_model(model_id)


def remove_model(provider: str, model_id: str) -> bool:
    """移除指定模型"""
    return _model_registry.remove_model(provider, model_id)


def remove_provider(provider: str) -> bool:
    """移除指定提供商及其所有模型"""
    return _model_registry.remove_provider(provider)


def clear_model_registry() -> None:
    """清空模型注册表"""
    _model_registry.clear()


__all__ = [
    "ModelRegistry",
    "register_model", "get_model",
    "get_models_by_provider", "list_providers",
    "list_all_models", "list_all_models_flat", "find_model_by_id",
    "register_models_from_dict", "remove_model", "remove_provider",
    "clear_model_registry",
]
