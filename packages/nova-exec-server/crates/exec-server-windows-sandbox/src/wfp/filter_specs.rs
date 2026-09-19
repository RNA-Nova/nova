use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::FWPM_LAYER_ALE_AUTH_CONNECT_V4;
use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::FWPM_LAYER_ALE_AUTH_CONNECT_V6;
use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::FWPM_LAYER_ALE_RESOURCE_ASSIGNMENT_V4;
use windows_sys::Win32::NetworkManagement::WindowsFilteringPlatform::FWPM_LAYER_ALE_RESOURCE_ASSIGNMENT_V6;
use windows_sys::Win32::Networking::WinSock::IPPROTO_ICMP;
use windows_sys::Win32::Networking::WinSock::IPPROTO_ICMPV6;
use windows_sys::core::GUID;

#[derive(Clone, Copy)]
pub(super) enum ConditionSpec {
    User,
    Protocol(u8),
    RemotePort(u16),
}

#[derive(Clone, Copy)]
pub(super) struct FilterSpec {
    pub(super) key: GUID,
    pub(super) name: &'static str,
    pub(super) description: &'static str,
    pub(super) layer_key: GUID,
    pub(super) conditions: &'static [ConditionSpec],
}

pub(super) const FILTER_SPECS: &[FilterSpec] = &[
    FilterSpec {
        key: GUID::from_u128(0xf451d2b3_7ca6_4f1d_938d_0db63acdba0f),
        name: "nova_wfp_icmp_connect_v4",
        description: "Block sandbox-account ICMP connect v4",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        conditions: &[
            ConditionSpec::User,
            ConditionSpec::Protocol(IPPROTO_ICMP as u8),
        ],
    },
    FilterSpec {
        key: GUID::from_u128(0xd57af5cb_e895_468d_82e3_dfe04417e0a5),
        name: "nova_wfp_icmp_connect_v6",
        description: "Block sandbox-account ICMP connect v6",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        conditions: &[
            ConditionSpec::User,
            ConditionSpec::Protocol(IPPROTO_ICMPV6 as u8),
        ],
    },
    FilterSpec {
        key: GUID::from_u128(0x71a1594c_0a05_4f8f_92e9_88f63b585cee),
        name: "nova_wfp_icmp_assign_v4",
        description: "Block sandbox-account ICMP resource assignment v4",
        layer_key: FWPM_LAYER_ALE_RESOURCE_ASSIGNMENT_V4,
        conditions: &[
            ConditionSpec::User,
            ConditionSpec::Protocol(IPPROTO_ICMP as u8),
        ],
    },
    FilterSpec {
        key: GUID::from_u128(0xbdaab59e_4642_426b_9d46_8ccd17f85f05),
        name: "nova_wfp_icmp_assign_v6",
        description: "Block sandbox-account ICMP resource assignment v6",
        layer_key: FWPM_LAYER_ALE_RESOURCE_ASSIGNMENT_V6,
        conditions: &[
            ConditionSpec::User,
            ConditionSpec::Protocol(IPPROTO_ICMPV6 as u8),
        ],
    },
    // NAME_RESOLUTION_CACHE filters are intentionally omitted because ordinary
    // static filter shapes returned FWP_E_OUT_OF_BOUNDS during validation.
    FilterSpec {
        key: GUID::from_u128(0x4593e981_340f_4ec2_bf8c_46fd00a6135b),
        name: "nova_wfp_dns_53_v4",
        description: "Block sandbox-account DNS TCP or UDP port 53 v4",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(53)],
    },
    FilterSpec {
        key: GUID::from_u128(0xb357229a_c1bf_468d_b871_2d3cceaa40ec),
        name: "nova_wfp_dns_53_v6",
        description: "Block sandbox-account DNS TCP or UDP port 53 v6",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(53)],
    },
    FilterSpec {
        key: GUID::from_u128(0x23540d6f_fc68_4449_b7ea_1ea99279bc47),
        name: "nova_wfp_dns_853_v4",
        description: "Block sandbox-account DNS-over-TLS port 853 v4",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(853)],
    },
    FilterSpec {
        key: GUID::from_u128(0x1dcba3d9_835b_4a57_b9db_322ee4bbe29e),
        name: "nova_wfp_dns_853_v6",
        description: "Block sandbox-account DNS-over-TLS port 853 v6",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(853)],
    },
    FilterSpec {
        key: GUID::from_u128(0x9a8a9adf_356f_419d_bfd9_1920f4cb92d8),
        name: "nova_wfp_smb_445_v4",
        description: "Block sandbox-account SMB port 445 v4",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(445)],
    },
    FilterSpec {
        key: GUID::from_u128(0xbcba0959_5a10_49d1_a175_e281c27a38f0),
        name: "nova_wfp_smb_445_v6",
        description: "Block sandbox-account SMB port 445 v6",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(445)],
    },
    FilterSpec {
        key: GUID::from_u128(0x2eac4484_6475_4f12_93a0_4f6ea76f11f5),
        name: "nova_wfp_smb_139_v4",
        description: "Block sandbox-account SMB port 139 v4",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V4,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(139)],
    },
    FilterSpec {
        key: GUID::from_u128(0xbdad61a7_e7a8_4720_8534_5b5b60d6d707),
        name: "nova_wfp_smb_139_v6",
        description: "Block sandbox-account SMB port 139 v6",
        layer_key: FWPM_LAYER_ALE_AUTH_CONNECT_V6,
        conditions: &[ConditionSpec::User, ConditionSpec::RemotePort(139)],
    },
];
