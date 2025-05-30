#!/usr/bin/env python3
# Copyright (C) 2025 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from dataclasses import dataclass
from enum import Enum
from typing import assert_never, Literal, NotRequired, TypedDict

from cmk.agent_based.v2 import (
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Result,
    Service,
    SimpleSNMPSection,
    SNMPTree,
    State,
    StringTable,
)

from .lib import SNMP_DETECT


class HighAvailabilityMode(Enum):
    STANDALONE = "0"
    PRIMARY = "1"
    SECONDARY = "2"
    UNKNOWN = "3"

    def to_human_readable(self) -> str:
        match self:
            case HighAvailabilityMode.STANDALONE:
                return "standalone"
            case HighAvailabilityMode.PRIMARY:
                return "primary"
            case HighAvailabilityMode.SECONDARY:
                return "secondary"
            case HighAvailabilityMode.UNKNOWN:
                return "unknown"
            case _:
                assert_never(self)


class Health(Enum):
    UNKOWN = "0"
    # 1 Indicates that the node is in the process of becoming part of the high
    #   availability configuration.
    INITIALIZING = "1"
    # undocumented
    DOWN = "2"
    # 3 Indicates that the node is accessible and can function as either
    #   a primary or secondary node.
    FUNCTIONAL = "3"
    # 4 Indicates that one of the high availability monitored interfaces
    #   has failed because of a card or link failure. # This state triggers a
    #   failover.
    SOME_HA_MONITORED_INTERFACES_FAILED = "4"
    # undocumented
    MONITOR_FAIL = "5"
    # undocumented
    MONITOR_OK = "6"
    # 7 Indicates that all the interfaces of the node are
    #   unusable because the interfaces on which high
    #   availability monitoring is enabled are not connected
    #   or are manually disabled. This state triggers a failover.
    ALL_HA_MONITORED_INTERFACES_FAILED = "7"
    # 8 Indicates that the node is in listening mode. It does not
    #   participate in high availability transitions or transfer
    #   configuration from the peer node. This is a configured
    #   value, not a statistic.
    CONFIGURED_LISTENING_MODE = "8"
    # 9 Indicates that the high availability status of the node has been
    #   manually disabled. Synchronization and propagation cannot take
    #   place between the peer nodes.
    HA_STATUS_MANUALLY_DISABLED = "9"
    # 10 Indicates that the SSL card has failed. This state triggers a failover.
    SSL_CARD_FAILED = "10"
    # 11 Indicates that the route monitor has failed. This state triggers
    #    a failover.
    ROUTER_MONITORE_FAILED = "11"

    def to_human_readable(self) -> str:
        match self:
            case Health.UNKOWN:
                return "unknown"
            case Health.INITIALIZING:
                return "initializing"
            case Health.DOWN:
                return "down"
            case Health.FUNCTIONAL:
                return "functional"
            case Health.SOME_HA_MONITORED_INTERFACES_FAILED:
                return "some HA monitored interfaces failed"
            case Health.MONITOR_FAIL:
                return "monitorFail"
            case Health.MONITOR_OK:
                return "monitorOK"
            case Health.ALL_HA_MONITORED_INTERFACES_FAILED:
                return "all HA monitored interfaces failed"
            case Health.CONFIGURED_LISTENING_MODE:
                return "configured to listening mode (dumb)"
            case Health.HA_STATUS_MANUALLY_DISABLED:
                return "HA status manually disabled"
            case Health.SSL_CARD_FAILED:
                return "SSL card failed"
            case Health.ROUTER_MONITORE_FAILED:
                return "route monitor has failed"
            case _:
                assert_never(self)


@dataclass(frozen=True, kw_only=True)
class Section:
    our_ha_mode: HighAvailabilityMode
    peer_ha_mode: HighAvailabilityMode
    our_health: Health


def parse_netscaler_ha(string_table: StringTable) -> Section | None:
    if not string_table:
        return None
    first_row = string_table[0]
    return Section(
        our_ha_mode=HighAvailabilityMode(first_row[0]),
        peer_ha_mode=HighAvailabilityMode(first_row[1]),
        our_health=Health(first_row[2]),
    )


snmp_section_netscaler_ha = SimpleSNMPSection(
    name="netscaler_ha",
    parse_function=parse_netscaler_ha,
    fetch=SNMPTree(
        base=".1.3.6.1.4.1.5951.4.1.1",
        oids=[
            "6",  # sysHighAvailabilityMode
            "23.3",  # haPeerState
            "23.24",  # haCurState
        ],
    ),
    detect=SNMP_DETECT,
)


class DiscoveredParams(TypedDict):
    discovered_failover_mode: NotRequired[Literal["primary", "secondary", "unknown"]]


def discover_netscaler_ha(section: Section) -> DiscoveryResult:
    match section.our_ha_mode:
        case HighAvailabilityMode.STANDALONE:
            return
        case HighAvailabilityMode.PRIMARY:
            yield Service(parameters=DiscoveredParams(discovered_failover_mode="primary"))
        case HighAvailabilityMode.SECONDARY:
            yield Service(parameters=DiscoveredParams(discovered_failover_mode="secondary"))
        case HighAvailabilityMode.UNKNOWN:
            yield Service(parameters=DiscoveredParams(discovered_failover_mode="unknown"))
        case _:
            assert_never(section.our_ha_mode)


class CheckParams(DiscoveredParams):
    failover_monitoring: (
        tuple[
            Literal["disabled"],
            # unused, just needed due to CascadingSingleChoice form spec
            None,
        ]
        | tuple[
            Literal["use_discovered_failover_mode"],
            # unused, just needed due to CascadingSingleChoice form spec
            None,
        ]
        | tuple[Literal["explicit_failover_mode"], Literal["primary", "secondary"]]
    )


def check_netscaler_ha(params: CheckParams, section: Section) -> CheckResult:
    match section.our_ha_mode:
        case HighAvailabilityMode.STANDALONE:
            return
        case HighAvailabilityMode.UNKNOWN:
            yield Result(
                state=State.UNKNOWN,
                summary="Failover mode: unknown",
            )
        case _:
            yield _check_our_failover_mode(section.our_ha_mode, params)

    yield _check_peer_mode(section.peer_ha_mode)
    yield Result(
        state=_health_to_monitoring_state(section.our_health),
        summary=f"Health: {section.our_health.to_human_readable()}",
    )


check_plugin_netscaler_ha = CheckPlugin(
    name="netscaler_ha",
    service_name="HA Node Status",
    discovery_function=discover_netscaler_ha,
    check_function=check_netscaler_ha,
    check_ruleset_name="netscaler_ha",
    check_default_parameters=CheckParams(failover_monitoring=("disabled", None)),
)


def _check_peer_mode(
    peer_ha_mode: HighAvailabilityMode,
) -> Result:
    if peer_ha_mode is HighAvailabilityMode.STANDALONE:
        return Result(
            state=State.WARN,
            summary="Peer is in standalone mode",
        )
    return Result(
        state=State.OK
        if peer_ha_mode
        in {
            HighAvailabilityMode.PRIMARY,
            HighAvailabilityMode.SECONDARY,
        }
        else State.WARN,
        summary=f"Peer failover mode: {peer_ha_mode.to_human_readable()}",
    )


def _check_our_failover_mode(
    our_failover_mode: Literal[HighAvailabilityMode.PRIMARY, HighAvailabilityMode.SECONDARY],
    params: CheckParams,
) -> Result:
    if params["failover_monitoring"][0] == "disabled":
        return Result(
            state=State.OK,
            summary=f"Failover mode: {our_failover_mode.to_human_readable()}",
        )
    if params["failover_monitoring"][0] == "use_discovered_failover_mode":
        return _check_our_failover_mode_against_discovered(
            our_failover_mode,
            params.get("discovered_failover_mode"),
        )
    if params["failover_monitoring"][0] == "explicit_failover_mode":
        expected_failover_mode = _parse_configured_or_discovered_failover_mode(
            params["failover_monitoring"][1]
        )
        return (
            Result(
                state=State.OK,
                summary=f"Failover mode: {our_failover_mode.to_human_readable()}",
            )
            if our_failover_mode is expected_failover_mode
            else Result(
                state=State.CRIT,
                summary=f"Failover mode: {our_failover_mode.to_human_readable()}, expected: {expected_failover_mode.to_human_readable()}",
            )
        )
    assert_never(params["failover_monitoring"])


def _check_our_failover_mode_against_discovered(
    our_failover_mode: Literal[HighAvailabilityMode.PRIMARY, HighAvailabilityMode.SECONDARY],
    discovered_failover_mode: Literal["primary", "secondary", "unknown"] | None,
) -> Result:
    if not discovered_failover_mode:
        return Result(
            state=State.UNKNOWN,
            summary="Failover monitoring is configured to use the discovered failover mode, "
            "but no discovered failover mode is available. Please re-discover.",
        )
    expected_failover_mode = _parse_configured_or_discovered_failover_mode(discovered_failover_mode)
    if expected_failover_mode is HighAvailabilityMode.UNKNOWN:
        return Result(
            state=State.UNKNOWN,
            summary="Failover monitoring is configured to use the discovered failover mode, "
            f"but the failover mode was {expected_failover_mode.to_human_readable()} when the last discovery ran. "
            "Please re-discover.",
        )
    return (
        Result(
            state=State.OK,
            summary=f"Failover mode: {our_failover_mode.to_human_readable()}",
        )
        if our_failover_mode is expected_failover_mode
        else Result(
            state=State.CRIT,
            summary=f"Failover mode: {our_failover_mode.to_human_readable()}, failover detected",
        )
    )


def _parse_configured_or_discovered_failover_mode(
    value: Literal["primary", "secondary", "unknown"],
) -> Literal[
    HighAvailabilityMode.PRIMARY, HighAvailabilityMode.SECONDARY, HighAvailabilityMode.UNKNOWN
]:
    match value:
        case "primary":
            return HighAvailabilityMode.PRIMARY
        case "secondary":
            return HighAvailabilityMode.SECONDARY
        case "unknown":
            return HighAvailabilityMode.UNKNOWN
        case _:
            assert_never(value)


def _health_to_monitoring_state(health: Health) -> State:
    match health:
        case Health.UNKOWN:
            return State.WARN
        case Health.INITIALIZING:
            return State.WARN
        case Health.DOWN:
            return State.CRIT
        case Health.FUNCTIONAL:
            return State.OK
        case Health.SOME_HA_MONITORED_INTERFACES_FAILED:
            return State.CRIT
        case Health.MONITOR_FAIL:
            return State.WARN
        case Health.MONITOR_OK:
            return State.WARN
        case Health.ALL_HA_MONITORED_INTERFACES_FAILED:
            return State.CRIT
        case Health.CONFIGURED_LISTENING_MODE:
            return State.WARN
        case Health.HA_STATUS_MANUALLY_DISABLED:
            return State.WARN
        case Health.SSL_CARD_FAILED:
            return State.CRIT
        case Health.ROUTER_MONITORE_FAILED:
            return State.CRIT
        case _:
            assert_never(health)
