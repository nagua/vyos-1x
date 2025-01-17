#!/usr/bin/env python3
#
# Copyright (C) 2018-2021 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# TODO: merge with show_dhcpv6.py

from json import dumps
from argparse import ArgumentParser
from ipaddress import ip_address
from tabulate import tabulate
from sys import exit
from collections import OrderedDict
from datetime import datetime

from isc_dhcp_leases import Lease, IscDhcpLeases

from vyos.config import Config
from vyos.util import is_systemd_service_running

lease_file = "/config/dhcpd.leases"
pool_key = "shared-networkname"

lease_display_fields = OrderedDict()
lease_display_fields['ip'] = 'IP address'
lease_display_fields['hardware_address'] = 'Hardware address'
lease_display_fields['state'] = 'State'
lease_display_fields['start'] = 'Lease start'
lease_display_fields['end'] = 'Lease expiration'
lease_display_fields['remaining'] = 'Remaining'
lease_display_fields['pool'] = 'Pool'
lease_display_fields['hostname'] = 'Hostname'

lease_valid_states = ['all', 'active', 'free', 'expired', 'released', 'abandoned', 'reset', 'backup']

def in_pool(lease, pool):
    if pool_key in lease.sets:
        if lease.sets[pool_key] == pool:
            return True

    return False

def utc_to_local(utc_dt):
    return datetime.fromtimestamp((utc_dt - datetime(1970,1,1)).total_seconds())

def get_lease_data(lease):
    data = {}

    # isc-dhcp lease times are in UTC so we need to convert them to local time to display
    try:
        data["start"] = utc_to_local(lease.start).strftime("%Y/%m/%d %H:%M:%S")
    except:
        data["start"] = ""

    try:
        data["end"] = utc_to_local(lease.end).strftime("%Y/%m/%d %H:%M:%S")
    except:
        data["end"] = ""

    try:
        data["remaining"] = lease.end - datetime.utcnow()
        # negative timedelta prints wrong so bypass it
        if (data["remaining"].days >= 0):
            # substraction gives us a timedelta object which can't be formatted with strftime
            # so we use str(), split gets rid of the microseconds
            data["remaining"] = str(data["remaining"]).split('.')[0]
        else:
            data["remaining"] = ""
    except:
        data["remaining"] = ""

    # currently not used but might come in handy
    # todo: parse into datetime string
    for prop in ['tstp', 'tsfp', 'atsfp', 'cltt']:
        if prop in lease.data:
            data[prop] = lease.data[prop]
        else:
            data[prop] = ''

    data["hardware_address"] = lease.ethernet
    data["hostname"] = lease.hostname

    data["state"] = lease.binding_state
    data["ip"] = lease.ip

    try:
        data["pool"] = lease.sets[pool_key]
    except:
        data["pool"] = ""

    return data

def get_leases(config, leases, state, pool=None, sort='ip'):
    # get leases from file
    leases = IscDhcpLeases(lease_file).get()

    # filter leases by state
    if 'all' not in state:
        leases = list(filter(lambda x: x.binding_state in state, leases))

    # filter leases by pool name
    if pool is not None:
        if config.exists_effective("service dhcp-server shared-network-name {0}".format(pool)):
            leases = list(filter(lambda x: in_pool(x, pool), leases))
        else:
            print("Pool {0} does not exist.".format(pool))
            exit(0)

    # should maybe filter all state=active by lease.valid here?

    # sort by start time to dedupe (newest lease overrides older)
    leases = sorted(leases, key = lambda lease: lease.start)

    # dedupe by converting to dict
    leases_dict = {}
    for lease in leases:
        # dedupe by IP
        leases_dict[lease.ip] = lease

    # convert the lease data
    leases = list(map(get_lease_data, leases_dict.values()))

    # apply output/display sort
    if sort == 'ip':
        leases = sorted(leases, key = lambda lease: int(ip_address(lease['ip'])))
    else:
        leases = sorted(leases, key = lambda lease: lease[sort])

    return leases

def show_leases(leases):
    lease_list = []
    for l in leases:
        lease_list_params = []
        for k in lease_display_fields.keys():
            lease_list_params.append(l[k])
        lease_list.append(lease_list_params)

    output = tabulate(lease_list, lease_display_fields.values())

    print(output)

def get_pool_size(config, pool):
    size = 0
    subnets = config.list_effective_nodes("service dhcp-server shared-network-name {0} subnet".format(pool))
    for s in subnets:
        ranges = config.list_effective_nodes("service dhcp-server shared-network-name {0} subnet {1} range".format(pool, s))
        for r in ranges:
            start = config.return_effective_value("service dhcp-server shared-network-name {0} subnet {1} range {2} start".format(pool, s, r))
            stop = config.return_effective_value("service dhcp-server shared-network-name {0} subnet {1} range {2} stop".format(pool, s, r))

            # Add +1 because both range boundaries are inclusive
            size += int(ip_address(stop)) - int(ip_address(start)) + 1

    return size

def show_pool_stats(stats):
    headers = ["Pool", "Size", "Leases", "Available", "Usage"]
    output = tabulate(stats, headers)

    print(output)

if __name__ == '__main__':
    parser = ArgumentParser()

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--leases", action="store_true", help="Show DHCP leases")
    group.add_argument("-s", "--statistics", action="store_true", help="Show DHCP statistics")
    group.add_argument("--allowed", type=str, choices=["sort", "state"], help="Show allowed values for argument")

    parser.add_argument("-p", "--pool", type=str, help="Show lease for specific pool")
    parser.add_argument("-S", "--sort", type=str, default='ip', help="Sort by")
    parser.add_argument("-t", "--state", type=str, nargs="+", default=["active"], help="Lease state to show (can specify multiple with spaces)")
    parser.add_argument("-j", "--json", action="store_true", default=False, help="Produce JSON output")

    args = parser.parse_args()

    conf = Config()

    if args.allowed == 'sort':
        print(' '.join(lease_display_fields.keys()))
        exit(0)
    elif args.allowed == 'state':
        print(' '.join(lease_valid_states))
        exit(0)
    elif args.allowed:
        parser.print_help()
        exit(1)

    if args.sort not in lease_display_fields.keys():
        print(f'Invalid sort key, choose from: {list(lease_display_fields.keys())}')
        exit(0)

    if not set(args.state) < set(lease_valid_states):
            print(f'Invalid lease state, choose from: {lease_valid_states}')
            exit(0)

    # Do nothing if service is not configured
    if not conf.exists_effective('service dhcp-server'):
        print("DHCP service is not configured.")
        exit(0)

    # if dhcp server is down, inactive leases may still be shown as active, so warn the user.
    if not is_systemd_service_running('isc-dhcp-server.service'):
        print("WARNING: DHCP server is configured but not started. Data may be stale.")

    if args.leases:
        leases = get_leases(conf, lease_file, args.state, args.pool, args.sort)

        if args.json:
            print(dumps(leases, indent=4))
        else:
            show_leases(leases)

    elif args.statistics:
        pools = []

        # Get relevant pools
        if args.pool:
            pools = [args.pool]
        else:
            pools = conf.list_effective_nodes("service dhcp-server shared-network-name")

        # Get pool usage stats
        stats = []
        for p in pools:
            size = get_pool_size(conf, p)
            leases = len(get_leases(conf, lease_file, state='active', pool=p))

            use_percentage = round(leases / size * 100) if size != 0 else 0

            if args.json:
                pool_stats = {"pool": p, "size": size, "leases": leases,
                              "available": (size - leases), "percentage": use_percentage}
            else:
                # For tabulate
                pool_stats = [p, size, leases, size - leases, "{0}%".format(use_percentage)]
            stats.append(pool_stats)

        # Print stats
        if args.json:
            print(dumps(stats, indent=4))
        else:
            show_pool_stats(stats)

    else:
        parser.print_help()
        exit(1)
