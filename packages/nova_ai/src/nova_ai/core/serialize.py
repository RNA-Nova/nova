
from  .content import TextContent,ImageContent
# 自定义序列化函数
def serialize_content(value):
    if isinstance(value, str):
        return value
    # 只要继承了 Mixin，就一定有 to_dict() 方法
    return[item.to_dict() for item in value]

# 自定义反序列化函数 (如果你需要 from_json 功能的话)
def deserialize_content(value):
    if isinstance(value, str):
        return value
    res =[]
    for item in value:
        if item.get("type") == "text":
            res.append(TextContent.from_dict(item))
        else:
            res.append(ImageContent.from_dict(item))
    return res