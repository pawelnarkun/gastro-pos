# core/views/auth.py
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from core.models import Station, Employee
from .utils import (
    log_event, get_station_team, set_station_team, 
    clear_station_team, get_current_leader, get_referer
)

def station_selection(request):
    stations = Station.objects.all().order_by('station_type', 'name')
    return render(request, 'station_selection.html', {'stations': stations})

def station_login(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    employees = Employee.objects.filter(is_active=True)
    if station.station_type == 'CASHIER':
        employees = employees.filter(can_work_cashier=True)
    elif station.station_type == 'KITCHEN':
        employees = employees.filter(can_work_kitchen=True)

    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        pin = request.POST.get('pin', '')
        emp = get_object_or_404(Employee, id=employee_id)
        
        if emp.pin and emp.pin != pin:
            return render(request, 'station_login.html', {
                'station': station, 'employees': employees, 'error': 'Błędny PIN!'
            })

        team_data = get_station_team(request, station_slug)
        if not team_data:
            set_station_team(request, station_slug, emp.id, [emp.id])
        else:
            members = team_data['members_ids']
            if emp.id not in members:
                members.append(emp.id)
            set_station_team(request, station_slug, team_data['leader_id'], members)
        
        log_event(station, 'LOGIN', employee=emp, details="Pracownik zalogował się do stacji.")

        if station.station_type == 'CASHIER':
            return redirect('cashier', station_slug=station.slug)
        elif station.station_type == 'KITCHEN':
            return redirect('kitchen', station_slug=station.slug)
        else:
            return redirect('station_selection')

    return render(request, 'station_login.html', {'station': station, 'employees': employees})

def station_logout(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    log_event(station, 'LOGOUT', employee=leader, details="Wylogowano cały zespół (zamknięcie zmiany).")
    clear_station_team(request, station_slug)
    return redirect('station_login', station_slug=station_slug)

def station_switch_leader(request, station_slug, employee_id):
    station = get_object_or_404(Station, slug=station_slug)
    team_data = get_station_team(request, station_slug)
    if team_data:
        target_id = int(employee_id)
        members = team_data.get('members_ids', [])
        if target_id in members:
            set_station_team(request, station_slug, target_id, members)
            new_leader = Employee.objects.filter(id=target_id).first()
            if new_leader:
                log_event(station, 'LEADER', employee=new_leader, details=f"Przejął rolę Lidera.")
    return redirect(get_referer(request))

@require_POST
def station_logout_user(request, station_slug, employee_id):
    station = get_object_or_404(Station, slug=station_slug)
    data = get_station_team(request, station_slug)
    if not data:
        return redirect('station_login', station_slug=station_slug)

    members = data.get('members_ids', [])
    leader_id = data.get('leader_id')
    target_id = int(employee_id)

    emp_leaving = Employee.objects.filter(id=target_id).first()
    if emp_leaving:
        log_event(station, 'LOGOUT', employee=emp_leaving, details="Wylogowanie pojedynczego pracownika.")

    if target_id in members:
        members.remove(target_id)
    
    if not members:
        clear_station_team(request, station_slug)
        return redirect('station_login', station_slug=station_slug)

    new_leader_id = leader_id
    if leader_id == target_id:
        new_leader_id = members[0]
        auto_leader = Employee.objects.filter(id=new_leader_id).first()
        if auto_leader:
             log_event(station, 'LEADER', employee=auto_leader, details="Automatyczne przejęcie lidera po wylogowaniu poprzednika.")
            
    set_station_team(request, station_slug, new_leader_id, members)
    return redirect(get_referer(request))