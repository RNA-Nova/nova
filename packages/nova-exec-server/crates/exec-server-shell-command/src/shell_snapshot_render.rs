// 对位 codex 39f4277087 codex-rs/shell-command/src/shell_snapshot_render.rs
//! 序列化已准备的快照值并套用 export 名称策略，全程不执行 shell 代码。
//! 原生声明保持不变，除非凭据阶段提供了替换值。

use super::capture::CapturedSnapshot;
use nova_exec_server_protocol_core::config_types::ShellEnvironmentPolicy;
use nova_exec_server_protocol_core::shell_environment::create_env_from_vars;

impl CapturedSnapshot<'_> {
    /// 渲染原生函数、选项与别名；环境由 executor 单独恢复。
    pub fn render_state(&self) -> String {
        format!("{}{}", self.state, self.aliases)
    }

    /// 仅持久化策略放行的 export。显式覆盖值由命令环境提供；
    /// 绝不保留其捕获到的原始值。
    pub fn render_script(&self, policy: &ShellEnvironmentPolicy) -> String {
        // nova 的 create_env_from_vars 比锚点少一个 thread_id 参数，此处少传一参。
        let allowed = create_env_from_vars(
            self.exports
                .iter()
                .map(|export| (export.key.to_string(), String::new())),
            policy,
        );
        let mut script = self.render_state();
        script.push_str("# exports (native declarations)\n");
        for export in &self.exports {
            if allowed.contains_key(export.key) && !policy.r#set.contains_key(export.key) {
                script.push_str(&export.source);
            }
        }
        script
    }
}

#[derive(Default)]
pub(super) struct Value {
    pub parts: Vec<ValuePart>,
}

pub(super) enum ValuePart {
    Literal(String),
    Credential { key: String },
}

pub(super) enum Export<'a> {
    Captured(&'a str),
    Assignment {
        declaration: &'a str,
        value: Value,
    },
    Array {
        prefix: &'a str,
        elements: Vec<Value>,
        suffix: &'a str,
    },
    ArrayBinding {
        key: &'a str,
        declaration: &'a str,
        suffix: &'a str,
    },
}

impl Value {
    fn render(&self) -> Option<String> {
        let mut output = String::new();
        for part in &self.parts {
            match part {
                ValuePart::Literal(value) => {
                    output.push_str(shlex::try_quote(value).ok()?.as_ref())
                }
                ValuePart::Credential { key } => {
                    output.push_str(&format!("\"${{{key}-}}\""));
                }
            }
        }
        if output.is_empty() {
            output.push_str("''");
        }
        Some(output)
    }
}

pub(super) fn render(state: &str, aliases: &str, exports: &[Export<'_>]) -> Option<String> {
    let mut output = format!("{state}{aliases}");
    if !exports.is_empty() {
        output.push_str("# exports (native declarations)\n");
    }
    for export in exports {
        match export {
            Export::Captured(source) => output.push_str(source),
            Export::Assignment { declaration, value } => {
                output.push_str(&format!("{declaration}={}\n", value.render()?));
            }
            Export::Array {
                prefix,
                elements,
                suffix,
            } => {
                let elements = elements
                    .iter()
                    .map(Value::render)
                    .collect::<Option<Vec<_>>>()?;
                output.push_str(&format!("{prefix}({}){suffix}", elements.join(" ")));
            }
            Export::ArrayBinding {
                key,
                declaration,
                suffix,
            } => {
                output.push_str(&format!(
                    "if [ \"${{{key}+x}}\" = x ]; then\n{declaration}{}\nfi\n",
                    suffix.trim_end()
                ));
            }
        }
    }
    Some(output)
}
