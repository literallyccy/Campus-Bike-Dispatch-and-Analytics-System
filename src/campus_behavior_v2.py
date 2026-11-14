"""Linked synthetic student-day agendas. Parameters are assumptions, not estimates."""
import math


def generate_day(day,rng,scenario,nodes,groups,distances,pick,add_demand,activities,weather_factor,variation):
    weekend=day%7>=5
    count=round(scenario['weekend_students' if weekend else 'weekday_students']*variation)
    for i in range(count):
        home=groups['dormitory'][i%len(groups['dormitory'])]
        actor=f'SYN-STUDENT-{i:05d}'
        location=home;available=day*86400+6*3600
        # One day-level mode preference avoids independent one-way mode choices
        # systematically leaving bicycles at cafeterias every evening.
        cycling=rng.random()<scenario['cycling_preference']*weather_factor
        steps=0
        teaching=pick('teaching',home)

        def visit(destination,departure_hour,until_hour,purpose):
            nonlocal location,available,steps
            if destination==location:return
            departure=max(int(day*86400+departure_hour*3600+rng.uniform(-180,180)),available)
            distance=distances[location,destination]
            # Feasible even if a cycling attempt fails and this leg is walked.
            arrival=departure+math.ceil(distance/scenario['walking_speed_mps'])+120
            end=max(int(day*86400+until_hour*3600),arrival+300)
            steps+=1;activity=f'ACT2-{day+1:03d}-{i:05d}-{steps:02d}'
            demand=add_demand(departure,location,destination,purpose,activity) if cycling and distance>=scenario['minimum_cycling_distance_m'] else None
            activities.append(dict(activity_id=activity,actor_id=actor,day_index=day,step=steps,
                category=purpose,origin_node=location,activity_node=destination,return_node=home,
                departure_s=departure,scheduled_start_s=arrival,scheduled_end_s=end,
                outbound_demand_id=demand,inbound_demand_id=None,day_cycling_preference=cycling,
                calendar='synthetic relative weekday, not an actual timetable',is_synthetic=True))
            location=destination;available=end

        if rng.random()<.25:visit(pick('dining',home),6.75,7.15,'breakfast')
        if weekend:
            morning=rng.choices(['library','sports','dormitory','gate_transfer'],[.35,.20,.20,.25])[0]
            if morning!='dormitory':visit(pick(morning,location),8.7,11.2,'weekend_'+morning)
        else:
            if rng.random()<.84:
                # Most classroom stays cover both morning blocks; staggered
                # cohorts prevent all buildings emptying at one global minute.
                start=rng.choices([7.35,9.35],[.75,.25])[0]
                visit(teaching,start,11.5+rng.uniform(-.08,.08),'morning_classes')
            else:visit(pick(rng.choices(['library','research'],[.8,.2])[0],home),8.2,11.2,'morning_study')
        lunch=rng.choices(['dining','dormitory'],[.80,.20])[0]
        visit(home if lunch=='dormitory' else pick('dining',location),11.6,12.15,'lunch')
        if location!=home and rng.random()<.82:visit(home,12.20,12.85,'noon_rest')
        if weekend:
            afternoon=rng.choices(['library','sports','gate_transfer','dormitory'],[.35,.25,.25,.15])[0]
            if afternoon!='dormitory':visit(pick(afternoon,location),13.0,16.8,'weekend_'+afternoon)
        elif rng.random()<.76:
            visit(teaching,rng.choices([12.95,14.95],[.72,.28])[0],17.05+rng.uniform(-.12,.12),'afternoon_classes')
        else:visit(pick(rng.choices(['library','research'],[.8,.2])[0],location),13.1,16.9,'afternoon_study')
        dinner=pick('gate_transfer' if rng.random()<(.15 if weekend else .07) else 'dining',location)
        outside=nodes[dinner]['function_type']=='gate_transfer'
        visit(dinner,17.25,18.5 if outside else 18.05,'dinner_outside' if outside else 'dinner')
        evening=rng.choices(['library','sports','dormitory'],[.48,.17,.35] if not weekend else [.35,.25,.40])[0]
        if evening!='dormitory':
            visit(pick(evening,location),18.1,21.3 if evening=='library' else 20.1,'evening_'+evening)
            visit(home,21.5 if evening=='library' else 20.3,23.7,'return_to_dormitory')
        else:visit(home,18.1,23.7,'return_to_dormitory')
