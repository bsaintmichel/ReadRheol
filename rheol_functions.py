import io
import numpy as np
import pandas as pd
from scipy.integrate import cumtrapz
from scipy import stats
from scipy.signal import find_peaks

from bokeh.plotting import figure, row, show, column
from bokeh.models import ColumnDataSource, LogColorMapper
from bokeh.transform import log_cmap
from bokeh.io import output_notebook
from bokeh.palettes import magma

import time

##############################################################################################
## TA FUNCTIONS --------------------------------------------------------------
ta_mapper = {'Step time' : 'time', 'Time' : 'time_global', 'Shear rate' : 'shearrate', 'Stress' : 'stress', 'Strain' : 'strain', 'Viscosity' : 'viscosity', 
                'Storage modulus' : 'gprime', 'Loss modulus' : 'gsecond', 'Frequency' : 'freq', 'Axial force': 'normalforce', 'Gap': 'gap', 'Temperature': 'temp', 'Torque': 'torque'}

def _read_TA(file_url):
    with open(file_url) as file:
        line_str = file.readline()
        is_data_line = False
        step_names = []
        all_data = []
        
        
        # For TA, we will do small PD DataFrames for each step then merge them
        while line_str:
            line_str = file.readline() 
            
            # Gather some constants
            if 'Stress constant' in line_str:
                stress_constant = float(line_str.split('\t')[1].split(' ')[0])
            if 'Strain constant' in line_str:
                strain_constant = float(line_str.split('\t')[1].split(' ')[0])  

            # Fetch step names
            if 'Procedure name' in line_str:
                    step_names.append(line_str.split('\t')[1].rstrip()) # First line is stupid ...
                    while 'proceduresegments' not in line_str:
                        line_str = file.readline().strip()
                        step_names.append(line_str)
                    step_names.pop()

            # Handle the actual data
            if is_data_line and line_str == '\n': # Switch off is_data "mode" at end of each step
                is_data_line = False
                now_data = pd.read_table(io.StringIO(data_str), delimiter='\t', skip_blank_lines=True, skiprows=[0,2])
                all_data.append(now_data)

            elif '[step]' in line_str: # Switch on is_data "mode"
                is_data_line = True
                data_str = ''
            elif is_data_line:
                data_str = data_str + line_str 

    return all_data, step_names, (stress_constant, strain_constant)

def _format_TA(all_data, step_names, constants):

        for step in range(len(all_data)):
            all_data[step]['name'] = step_names[step]
            all_data[step]['step'] = step

            # Oscillatory stuff
            is_osc_step = 'Oscillation stress' in all_data[step].columns

            if is_osc_step:
                all_data[step]['Stress'] = all_data[step]['Oscillation stress']
                all_data[step]['Strain'] = all_data[step]['Oscillation strain']
                all_data[step]['Viscosity'] = np.nan
                all_data[step]['Shear rate'] = np.nan
            if not is_osc_step:
                all_data[step]['Frequency'] = np.nan
                all_data[step]['Storage modulus'] = np.nan
                all_data[step]['Loss modulus'] = np.nan

            # Temperature stuff
            no_temp = 'Temperature' not in all_data[step].columns
            if no_temp:
                all_data[step]['Temperature'] = np.nan 

            # Gap
            no_gap = 'Gap' not in all_data[step].columns
            if no_gap:
                all_data[step]['Gap'] = np.nan

            # Normal force
            no_NormalForce = 'Axial force' not in all_data[step].columns
            if no_NormalForce:
                all_data[step]['Axial force'] = np.nan

            # Torque
            no_Torque = 'Torque' not in all_data[step].columns
            if no_Torque:       
                all_data[step]['Torque'] = all_data[step]['Stress']/constants[0]
            else:
                all_data[step]['Stress'] = all_data[step]['Torque']*constants[0]*1e-6 # Conversion constant in Nm/Pa but torque in txt file in ??N.m ...

            # Strain (REALLY ?!)
            no_Strain = 'Strain' not in all_data[step].columns and 'Oscillation strain' not in all_data[step].columns
            if no_Strain and 'Displacement' in all_data[step].columns:
                all_data[step]['Strain'] = (all_data[step]['Displacement'] - all_data[step]['Displacement'].iloc[0])*constants[1]*100
            elif no_Strain and 'Shear rate' in all_data[step].columns:
                all_data[step]['Strain'] = np.insert(cumtrapz(x=all_data[step]['Step time'], y=all_data[step]['Shear rate']), 0, 0)*100
            elif no_Strain:
                print('>> format_TA : Could not infer strain from data at step n??' + str(step))
                all_data[step]['Strain'] = np.nan
            else:
                all_data[step]['Strain'] = (all_data[step]['Strain'] - all_data[step]['Strain'].iloc[0])*100

        # Final tweaks
        all_data = pd.concat(all_data)
        all_data = all_data.rename(columns=ta_mapper)
        all_data['steptype'] = ''
        all_data['status'] = ''
        all_data = all_data[['step', 'name', 'steptype', 'time', 'time_global', 'shearrate', 'strain', 'stress', 'freq', 'gprime', 'gsecond', 'normalforce', 'temp', 'gap', 'status']]

        return all_data

###############################################################################################
## ANTON PAAR FUNCTIONS --------------------------------------------------------------

antonpaar_mapper_en = {'Time' : 'time_global', 'Interval Time' : 'time', 'Shear Rate' : 'shearrate', 'Shear Stress' : 'stress', 'Shear Strain' : 'strain', 'Frequency' : 'freq', 
                'Storage Modulus' : 'gprime', 'Loss Modulus' : 'gsecond', 'Normal Force' : 'normalforce', 'Torque' : 'torque', 'Status' : 'status', 'Temperature': 'temp'}
antonpaar_mapper_fr = {'Temps' : 'time_global', 'Intervalle Temps' : 'time', 'Gradient de Cisaillement' : 'shearrate', 'Contrainte de Cisaillement' : 'stress', 'D??formation de Cisaillement' : 'strain', 'Fr??quence' : 'freq', 
                'Module de Stockage' : 'gprime', 'Module de Perte' : 'gsecond', 'Force Normale' : 'normalforce', 'Couple' : 'torque', 'Etat' : 'status', 'Temp??rature': 'temp'}
                # TODO : check what is the name for 'Gap' in RheoCompass

def _read_antonpaar(file_url):
    with open(file_url, encoding='utf-16-le') as file:
        line_str = file.readline()
        is_data_line = False
        reset_global_time = False  # Will be used to reset global time
        all_data = []
        step = 0

        # For Anton Paar, we build a list of DataFrames that we will concatenate later
        while line_str: # If there is still something to read in the file ...
            line_str = file.readline()  
            if is_data_line and line_str == '\n' or is_data_line and not line_str:
                is_data_line = False
                data_now = pd.read_table(io.StringIO(data_str.replace(',','.')), delimiter='\t', skip_blank_lines=True, header=0, skiprows=[1,2])
                data_now['step'] = step
                data_now['reset_global_time'] = reset_global_time
                step += 1
                reset_global_time = False
                all_data.append(data_now)
            elif is_data_line and '(invalid)' not in line_str: # Yeah, sometimes the rheometer is a bit playful ...
                data_str = data_str + line_str
            elif 'Point No.' in line_str:
                data_str = line_str
                is_data_line=True
            elif 'Test' in line_str: # New test means |> (play button) has been pressed in RheoCompass ==> New sample ==> Reset time_global
                reset_global_time = True

        all_data = pd.concat(all_data).reset_index(drop=True)
        return all_data    

def _format_antonpaar(df):
    # Add step n?? and global time. Drop Unnecessary columns
    
    df['name'] = '' # Anton Paar does not systematically give names to Actions (a pity ...)
    df['steptype'] = ''
    df['gap'] = np.nan # XXX to be fixed later
    df['normalforce'] = np.nan # XXX to be fixed later
    
    if 'Intervalle Temps' in df.columns:
        df = df.rename(columns=antonpaar_mapper_fr)
    elif 'Interval Time' in df.columns:
        df = df.rename(columns=antonpaar_mapper_en)
    else:
        print('> _format_antonpaar : Looks like you are using a Funky Language that I don''t understand ! ')
        raise ValueError

    df = _fix_globaltime_antonpaar(df)
    df = df[[ 'step', 'name', 'steptype', 'time', 'time_global', 'shearrate', 'strain', 'stress', 'freq', 'gprime', 'gsecond', 'normalforce', 'temp', 'gap', 'status']]
    return df

def fix_strain_antonpaar(df, steps=None):
    """
    Function that fixes the strain in Anton Paar data 

    ARGS : 
    - df [PANDAS.DATAFRAME] : your not-necessary sliced Malvern data 
    - steps [NONE or LIST] : the steps where you wish to fix your stress
    (default : all steps are fixed)

    OUTPUT :
    - df [PANDAS.DATAFRAME] : the corrected data (d'uh !)

    NOTE : 
    If you want to fix your strain "for good" (i.e. in the original DataFrame),
    do not slice it first with rh.slice and fix the "sliced" df, because
    obviously the original dataframe will not be fixed...
    """
    if steps is None:
        steps = np.unique(df['steps'])

    for s in steps:
        indexer = df['step'] == s
        tm, sr = df.loc[indexer, 'time'], df.loc[indexer, 'shearrate']
        df.loc[indexer, 'strain'] = np.concatenate(([0], cumtrapz(tm, sr)))
    return df

def _fix_globaltime_antonpaar(df):
    """ 
    Function that fixes 'time_global'. I thought that this quantity
    thing was really global, but it seems that for each 'procedure' (i.e. vertical tab
    in Rheocompass), it resets. So I handle that here.
    """

    indices, = np.where(df['time_global'].diff() < 0)
    t_refs = df['time_global'].iloc[indices-1]

    for idx, t_ref in zip(indices, t_refs):
        if not df.loc[idx, 'reset_global_time']:
            df.loc[idx:,'time_global'] += t_ref

    return df

###############################################################################################
## MALVERN FUNCTIONS -------------------------------------------------------------------------

malvern_mapper =  {'Time (action)(s)': 'time',  'Time (sequence)(s)': 'time_global', 'Shear rate(s-??)': 'shearrate', 'Shear stress(Pa)': 'stress', 
                   'Shear strain(%)': 'strain', 'Frequency(Hz)': 'freq', 'Shear modulus (elastic component)(Pa)': 'gprime', 
                   'Shear modulus (viscous component)(Pa)': 'gsecond','Normal force(N)': 'normalforce', 'Gap(mm)': 'gap', 'Torque(N m)':'torque'}

    # For Malvern, well there ain't much to do ...

def _read_malvern(file_url, decim_sep=',', field_sep=';'):
    data = pd.read_csv(file_url, decimal=decim_sep, sep=field_sep, encoding='utf-8')
    return data

def _format_malvern(df):
    # Add step n?? and global time. Drop Unnecessary columns
    n = len(df)
    dt_long = np.diff(np.insert(df['Time (action)(s)'].to_numpy(), 0,0))
    new_step, action = np.zeros(n, dtype=int), df.iloc[0]['Action Name']
    for index in range(n):
        if df.iloc[index]['Action Name'] != action:
            new_step[index] = 1
            action = df.iloc[index]['Action Name']
    df['name'] = df['Action Name']
    df['step'] = np.cumsum(new_step).astype(int)
    df['steptype'] = ['']*n
    df['temp'] = np.ones(n)*np.nan # XXX to be fixed later
    df['status'] = ['']*n
    
 
    # Merge "complex" (oscillatory) and "normal" (flow) stress and strains
    osc_steps = ~df['Complex shear strain(%)'].isnull()
    df.loc[osc_steps, 'Shear strain(%)'] = df.loc[osc_steps, 'Complex shear strain(%)']
    df.loc[osc_steps, 'Shear stress(Pa)'] = df.loc[osc_steps, 'Complex shear stress(Pa)']
    df.drop(columns=['Action Name', 'Time (sequence)(s)', 'Complex shear strain(%)', 'Complex shear stress(Pa)'])

    # Rename columns
    df = df.rename(columns=malvern_mapper)
    df = df[['step', 'name', 'steptype', 'time', 'time_global', 'shearrate', 'strain', 'stress', 'freq', 'gprime', 'gsecond', 'normalforce', 'temp', 'gap', 'torque', 'status']]

    return df

def fix_stress_malvern_reversal(df, steps=None):
    """
    Function that fixes the stress for Malvern reversal
    data. Somehow they think stress should always be positive.

    ARGS : 
    - df [PANDAS.DATAFRAME] : your not-necessary sliced Malvern data 
    - steps [NONE or LIST] : the steps where you wish to fix your stress
    (default : all steps are fixed)

    OUTPUT :
    - df [PANDAS.DATAFRAME] : the corrected data (d'uh !)

    NOTE : 
    If you want to fix your stress "for good" (i.e. in the original DataFrame),
    do not slice it first with rh.slice and fix the "sliced" df, because
    obviously the original dataframe will not be fixed...
    """
    if steps is None:
        steps = np.unique(df['step'])

    for s in steps:
        strs = df.loc[df['step'] == s, 'stress'].to_numpy()
        strn = df.loc[df['step'] == s, 'strain'].to_numpy()
        strsdip, properties = find_peaks(-strs, prominence=0.5) # Crossing zero == sharp peak for minus strs

        if np.all(strs > 0):
            if len(strsdip) > 0: 
                potential_location = strsdip[np.argsort(properties['prominences'])[-1]]
                stress_ratio_at_potential_location = np.mean(strs[potential_location-1:potential_location+1])/np.max(np.abs(strs))
                if stress_ratio_at_potential_location < 0.05:
                        # Means we get close to 0 Pa and we brutally change sign 
                        # Most certainly what we wish to fix ...
                    strs[potential_location:] = -strs[potential_location:]
                    df.loc[df['step'] == s, 'stress'] = strs
            else: # If we fail to see to stress "dip" corresponding to the unwrapping
                print('fix_stress_malvern_reversal > Failed to unwrap stress at step ' + str(s) + ' : no clear stress dip.')
            if strn[-1] > 0: # If user has (maybe) used a forward step to reverse the stress
                print('fix_stress_malvern_reversal > Warning : step ' + str(s) + ' ends with a positive strain.')
                print('                            > Check if it is actually a reverse step')
        else: # If you are trying to fix something that has already been fixed ... do nothing.
            print('fix_stress_malvern_reversal > Step ' + str(s) + ' probably already reversed and contains negative values.')
    return df

###############################################################################################
## GENERAL FUNCTIONS -------------------------------------------------------------------------

def read_rheology(file_url): 
    """
    Global function to read rheology data

    ARGS : 
    - file_url [STRING] : path to file

    RETURNS : 
    - df [PANDAS.DATAFRAME] : formatted data to be further processed or plotted
    """
    with open(file_url) as file:
        header = file.readline(150)
        if 'Time (action)(s)' in header:
            print('read_rheology > ' + file_url + ' is a Malvern file')
            data = _format_malvern(_read_malvern(file_url, decim_sep=',', field_sep=';'))
        elif 'Filename' in header:
            print('read_rheology > ' + file_url + ' is a TA (Trios) file')
            data_raw, stepnames, csts = _read_TA(file_url)
            data = _format_TA(data_raw, stepnames, csts)
        else:
            file_utf16 = open(file_url, encoding='utf-16-le')
            header_16 = file_utf16.readline(150)
            file_utf16.close()
            if ('Test:' in header_16) or ('Projet:' in header_16) or ('Project:' in header_16):
                data = _format_antonpaar(_read_antonpaar(file_url))
                print('read_rheology > ' + file_url + ' is an Anton Paar file')
            else:
                print('read_rheology > Cannot detect file type')
                return None

    data = assign_steps(data)
    return data

def assign_steps(df): # Works for both Malvern and Anton Paar (normally)
    """
    Function that tries to automatically assign a step 
    to each step in your data based on what varies and what
    is kept constant and what data is NaN (e.g. in oscillatory there is
    no shear rate).
    This is experimental. Also : you can 'reassign' steps using ...
    well, `reassign_steps`

    ARGS : 
    - df [PANDAS.DATAFRAME] : your rheology data

    OUTPUT : 
    - df [PANDAS.DATAFRAME] : the reassigned data
    """
    nsteps = df.iloc[-1]['step']+1
    # for step in range(slice_no[-1]):
    for step in range(nsteps):
        is_now = np.logical_and(df['step'] == step, df['time'] > 3) # Also : past initial transient shenanigans.
        df_now = df[is_now]

        if all(np.isnan(df_now['gprime'])): # We have a "flow" step ##OR## df_now is empty

            # Quantities used to identify steps
            st_max, st_min, st_avg = np.abs(np.max(df_now['stress'])), np.abs(np.min(df_now['stress'])), np.abs(np.mean(df_now['stress']))
            sr_max, sr_min, sr_avg = np.abs(np.max(df_now['shearrate'])), np.abs(np.min(df_now['shearrate'])), np.abs(np.mean(df_now['shearrate']))
            
            # step strain thingies : if we don't move after 1s, it is a step strain
            late_times = df_now['time'] > 1
            all_nan_strains = df_now['strain'].isnull().all()
            late_strain = df_now[late_times]['strain']
            if len(late_strain) > 0 and not all_nan_strains: 
                step_stn, std_stn = np.abs(np.mean(late_strain) - df_now['strain'].first_valid_index()), np.std(late_strain)
            else:
                step_stn, std_stn = 0,0

            if st_avg < 1e-2: # No stress --> Rest step
                step_type = 'Rest'
            elif sr_min > 200: # High Strain rate, does not change too much : Preshear
                step_type = 'Preshear'
            elif sr_max > 10 and sr_min < 1 and (st_max - st_min) / st_min > 3: # Wide amplitude in shear rates and stress: probable flow curve
                step_type = 'Flowcurve'
            elif sr_avg > 1e-3 and sr_max - sr_min < 0.25*(sr_max): # OK now I am getting increasingly clueless ... probable shear startup (but unsure)
                step_type = 'Shearstartup'
            elif st_avg > 1e-2 and st_max - st_min < 0.05*(st_min): # Nonzero stress, kept constant (for a stress controlled rheometer): Creep
                step_type = 'Creep'
            elif step_stn > 3e-2 and std_stn < 1e-3  : # Well .. then I don't know.
                step_type = 'Stepstrain'
            else:
                step_type = 'Unknown'

            if df_now.empty:
                print('assign_steps > Step : ' + str(step) + ' too short for auto assignment')

        else: # We have an "oscillatory" step
            f_avg, f_std = np.abs(np.mean(df_now['freq'])), np.std(df_now['freq'])/np.abs(np.mean(df_now['freq']))
            amp_avg, amp_std = np.abs(np.mean(df_now['strain'])), np.std(df_now['strain'])/np.abs(np.mean(df_now['strain']))
            temp_var = np.max(df_now['temp']) - np.min(df_now['temp'])
            nan_temp = np.all(np.isnan(df_now['temp']))
            if f_std < 0.05 and amp_std < 0.05 and (temp_var < 2 or nan_temp) : # Nothing changes : Time Sweep
                step_type = 'Timesweep'
            elif f_std < 0.05 and amp_std < 0.05 and temp_var > 2:
                step_type = 'Temperaturesweep'
            elif f_std < 0.05 and amp_std > 0.5:
                step_type = 'Amplitudesweep'
            elif f_std > 0.05 and amp_std < 0.1:
                step_type = 'Freqsweep'
            else:
                step_type = 'Unknown'

        df.loc[df['step'] == step, 'steptype'] = step_type
    return df

def reassign_steps(df, steps, steptypes):
    """
    Function that reassigns the step type if you are not happy with what the 
    automatic programme has done

    ARGS : 
    - df [PANDAS.DATAFRAME] : your rheology data
    - steps [LIST] : list of steps n?? you want to reassign
    - steptypes [LIST of STR or STR] : list of step types you want to assign to the step n??s

    OUTPUT : 
    - df [PANDAS.DATAFRAME] : the reassigned data
    """
    if np.isscalar(steptypes):
        steptypes = [steptypes]*len(steps)
    for no, step in enumerate(steps):
        indices = df['step'] == step
        steptype = steptypes[no]
        df.loc[indices, 'steptype'] = steptype
        print('reassign_steps > step : ' + str(step) + ' | Reassigning as : ' + steptype)

    return df

def list_steps(df, step_type=None):
    """
    Function that finds and lists the step number(s) corresponding to a step type. 
    Or lists them if you specify nothing as second argument

    ARGS : 
    - df [PANDAS.DATAFRAME] : your global rheology data
    - step_type
      * [STRING], has to be one of : 'amplitudesweep', 'freqsweep', 'flowcurve', 'creep', 
      'shearstartup', 'rest', 'preshear', 'timesweep'
      * [NONE]

    OUTPUT : 
    - NONE if step_type is None 
    - MATCHES if step_type is str : a list of steps that match your query
    """
    # Note : step_type : 
    steps = np.unique(df['step'])
    if not step_type:
        print('------------- Step list in DataFrame --------------------')
        for step in steps:
            dfnow = df[df['step'] == step]
            iend = dfnow['time_global'].last_valid_index()
            steptype = '{:>15}'.format(dfnow.iloc[0]['steptype'])
            stepname = '{:>40}'.format(dfnow.iloc[0]['name'])
            if iend is not None:
                stepduration = '{:>10.2f}'.format(dfnow.loc[iend,'time'])
                time_global = '{:>10.2f}'.format(dfnow.loc[iend, 'time_global'])
                print('  * Step n??' + str(step) + ' \t : '+ stepname + '\t is a ' + steptype + ' | Duration : ' + str(stepduration) + ' s | Total time is : ' + str(time_global) + ' s')
            else:
                print('  * Step n??' + str(step) + ' \t : '+ stepname + '\t is a ' + steptype + ' | Is a bit mysterious')
        print('---------------------------------------------------------')
        return None
    else:
        aliases = {'asweep': 'amplitudesweep', 'ampsweep':'amplitudesweep', 'tsweep':'timesweep', 'fsweep':'freqsweep', 'frequencysweep':'freqsweep'}
        if step_type in aliases:
            step_type = aliases[step_type]
        unique_matches =  np.unique(df[df['steptype'].str.lower() == step_type]['step'])
        return list(unique_matches)

def slice(df, key):
    """ 
    Function that returns a subset of your data 
    based on a list of steps.

    ARGS : 
    - df [PANDAS.DATAFRAME] : your rheology data
    - steps [LIST] OR [STR] or [INT] : your step(s) [INT OR LIST] or step types [STR] you want to fetch

    OUTPUT :
    - sliced_df [PANDAS.DATAFRAME] : a (smaller) set of your rheology data containing
    what you want
    """
    if type(key) == str:
        steps = list_steps(df, step_type=key)
    elif type(key) == int:
        steps = [key]
    elif type(key) != list and not isinstance(key, np.ndarray):
        raise TypeError('rh.slice > Trying to index data with a ' + str(type(key)))
    else:
        steps = key
        
    sliced_df = pd.DataFrame()
    for step in steps:
        sliced_df = pd.concat((sliced_df, df[df['step'] == step]))

    if sliced_df.empty:
        print('slice > Warning : empty dataframe with slicing key ' + str(key))
    return sliced_df



#########################################################
## --------- COMPUTING SCIENTIFIC THINGS FUNCTION -----

def _fit_HB(rate, strs, fit_up_to=1e3, fit_from=1e-3):
    """
    Function that computes a fit to a Herschel_Bulkley (HB)
    law in log-log coordinates to try to extract the HB 
    parameters of flow curve data
    """
    rate, strs = rate.to_numpy(), strs.to_numpy()
    valid_shearrate = np.logical_and(rate > fit_from, rate < fit_up_to)
    rate, strs = rate[valid_shearrate], strs[valid_shearrate]
    strs0 = np.min(np.abs(strs))

    potential_ys, results = np.linspace(0.75*strs0, 1.25*strs0, 200), np.zeros((200,3))
    for idx, test_ys in enumerate(potential_ys):
        valid = strs - test_ys > 0
        x, y = np.log(rate[valid]), np.log(strs[valid] - test_ys)
        rs = stats.linregress(x,y)
        results[idx] = np.array([rs.slope, rs.intercept, rs.rvalue])
    best_fit = np.argmax(results[:,2])
    slope, K, ys = results[best_fit,0], np.exp(results[best_fit,1]), potential_ys[best_fit] 
    return ys, K, slope


##############################################################################
## -------- PLOTTING FUNCTIONS ###############################################
def darken(palette, factor=0.6):
    """
    Function that darkens a Bokeh palette or a single color
    by a given factor
    """
    isStr = False
    if type(palette) == str:
        isStr = True
        palette = [palette]
    
    rgb = [[int(c[1:3], 16),int(c[3:5], 16),int(c[5:7], 16)] for c in palette]
    dark_rgb = lambda factor : [[int(c[0]*factor), int(c[1]*factor), int(c[2]*factor)] for c in rgb]
    hexvals = ['#' +'{0:0{1}x}'.format(c[0]*256**2 + c[1]*256 + c[2],6) for c in dark_rgb(factor)]

    if isStr:
        return hexvals[0] # Returns a str if a str was input
    else:
        return hexvals # Returns a list if a list was input
 
def plot_flowcurve(df, fit_from=1e-3, fit_up_to=1e3):
    """ 
    Function to plot flow curve steps

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - fit_up_to [FLOAT] [OPTIONAL] : fit the flow curves up to some specific shear rate value 

    OUTPUT :
    - a figure (d'uh !)
    - FIT [Nx4 list] : the fit to your steps, with [step, sigma_Y, K, exponent]
    """   
    if len(df) > 0 and 'step' in df.keys():
        steps = np.unique(df['step'])
        fits_all = np.zeros((len(steps), 4))

        # Create and format figure
        f1 = figure(x_axis_type='log', y_axis_type='log', title='Flow Curve', tooltips=[('x','$x'),('y','$y')], width=500, height=400)
        f1.xaxis.axis_label, f1.yaxis.axis_label = '?? (1/s)', '?? (Pa)'
        cmap, cmap_line = magma(np.size(steps)+1), darken(magma(np.size(steps)+1))

        for no, step in enumerate(steps):
            # Check that step makes sense
            flow_curve = df[df['step'] == step]
            if flow_curve.iloc[0]['steptype'] != 'Flowcurve':
                print('plot_flowcurve >  Warning : Cannot confirm that step ' + str(step) + ' is a flow curve.')    
            ys, K, exponent = _fit_HB(flow_curve['shearrate'], flow_curve['stress'], fit_up_to=fit_up_to, fit_from=fit_from)
            fits_all[no,:] = [step, ys, K, exponent]

            # Plotting + Display + producing the fitted curve
            shearrates = flow_curve['shearrate']
            fitted_stress = ys + K*shearrates**exponent 
            f1.line(shearrates, fitted_stress, line_color=cmap_line[no])
            f1.scatter(x='shearrate', y='stress', marker='square', source=flow_curve, line_color=cmap_line[no], fill_color=cmap[no], legend_label="Flow curve, step " + str(step))    
            print('plot_flowcurve > fit for step '  + str(step) + ' : ?? = ' + '{:3.2f}'.format(ys) + ' + '  + '{:3.2f}'.format(K) + ' ??^(' + '{:3.2f}'.format(exponent) + ')')
        
        f1.legend.location = 'bottom_right'
        show(f1)
        time.sleep(0.5)

        return fits_all, f1
    else:
        print('plot_flowcurve > No flowcurve step found')
        return None, None

def plot_normalforce(df):
    """ 
    Function to plot flow normal forces (as a function of gamma_dot by default, but gamma also possible)

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - steps [LIST] : your step(s) you want to plot (can be only one step)

    OUTPUT :
    - a figure (d'uh !)
    """
    steps = np.unique(df['step'])

    # Create and format figure
    f1 = figure(x_axis_type='log', y_axis_type='linear', title='Normal Force', tooltips=[('x','$x'),('y','$y')])
    f1.y_range.start, f1.y_range.end = -1,1
    cmap, cmap_line = magma(np.size(steps)+1), darken(magma(np.size(steps)+1))

    
    for no, step in enumerate(steps):
        # Check that step makes sense
        df_now = df[df['step'] == step]
    
        if df_now.iloc[0]['steptype'] == 'Flowcurve':
            print('plot_normalforce > Step ' + str(step) + ' is a flow curve. Plotting N = f(gamma_dot)')
            f1.scatter(df_now['shearrate'], df_now['normalforce'], line_color=cmap_line[no], fill_color=cmap[no], marker='o', legend_label='Step ' + str(step))    
            f1.xaxis.axis_label, f1.yaxis.axis_label = '??^dot (1/s)', 'F_N (N)'
        else:
            print('plot_normalforce > Step ' + str(step) + ' not a flow curve. Plotting N = f(gamma)')
            f1.scatter(df_now['strain'], df_now['normalforce'], line_color=cmap[no], fill_color=cmap[no], marker='diamond',  legend_label='Step ' + str(step))    
            f1.xaxis.axis_label, f1.yaxis.axis_label = '?? (1)', 'F_N (N)'

    show(f1)
    time.sleep(0.5)

    return f1

def plot_asweep(df, plot_stress=False):
    """ 
    Function to plot amplitude sweeps

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - steps [LIST] : your step(s) you want to plot (can be only one step)

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        steps = np.unique(df['step'])
        f1 = figure(x_axis_type='log', y_axis_type='log', title='Amplitude Sweep', tooltips=[('x','$x'),('y','$y')], width=500, height=400)
        cmap, cmap_line = magma(np.size(steps)+1), darken(magma(np.size(steps)+1))

        for no, step in enumerate(steps):
            # Check that step makes sense
            amp_sweep = df[df['step'] == step]
            if amp_sweep.iloc[0]['steptype'] != 'Amplitudesweep':
                print('plot_asweep > Warning : Cannot confirm that step ' + str(step) + ' is an amplitude sweep')
            f1.scatter(x='strain', y='gprime' , marker='square', line_color=cmap[no], fill_color=cmap_line[no], source=amp_sweep, legend_label="G' , step " + str(step))
            f1.scatter(x='strain', y='gsecond', marker='o',      line_color=cmap[no], fill_color='lightgray', source=amp_sweep, legend_label="G'' , step " + str(step))
            if plot_stress: f1.scatter(x='strain', y='stress', marker='triangle', line_color=cmap_line[no], fill_color=cmap[no], source=amp_sweep, legend_label='?? , step ' + str(step))
        
        f1.xaxis.axis_label, f1.yaxis.axis_label = '?? (%)', 'G, ?? (Pa)'
        f1.legend.location = "bottom"

        show(f1)
        time.sleep(0.5)

        return f1
    else:
        print('plot_asweep > No amplitude sweep step in the sliced dataset')
        return None


def plot_fsweep(df, plot_stress=False):
    """ 
    Function to plot frequency sweeps

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - plot_stress [BOOL] [OPTIONAL] : add the global oscillatory stress to the plot

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        steps = np.unique(df['step'])
        f1 = figure(x_axis_type='log', y_axis_type='log', title='Frequency Sweep', tooltips=[('x','$x'),('y','$y')], width=500, height=400)
        cmap, cmap_line = magma(np.size(steps)+1), darken(magma(np.size(steps)+1))

        for no, step in enumerate(steps): 
                # Check that step makes sense
            freq_sweep = df[df['step'] == step]
            if freq_sweep.iloc[0]['steptype'] != 'Freqsweep':
                print('plot_fsweep > Cannot confirm that step ' + str(step) + ' is a frequency sweep')
            f1.scatter(x='freq', y='gprime' , marker='square', line_color=cmap_line[no], fill_color=cmap[no], source=freq_sweep, legend_label="G' , step " + str(step))
            f1.scatter(x='freq', y='gsecond', marker='o',      line_color=cmap_line[no], fill_color='lightgray', source=freq_sweep, legend_label="G'' , step " + str(step))
            if plot_stress: f1.scatter(x='frequency', y='stress', marker='triangle', line_color=cmap_line[no], fill_color=cmap[no], source=freq_sweep, legend_label='?? , step ' + str(step))
            
        f1.xaxis.axis_label, f1.yaxis.axis_label = 'f (Hz)', 'G, ?? (Pa)'
        f1.legend.location = 'bottom_right'

        show(f1)
        time.sleep(0.5)

        return f1
    else:
        print('plot_fsweep > No frequency sweep step in the sliced dataset')
        return None
    
def plot_tsweep(df, plot_stress=False, x_axis_type='log', y_axis_type='log'):
    """ 
    Function to plot time sweeps

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - plot_stress [BOOL] [OPTIONAL] : add the global oscillatory stress to the plot

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        steps = np.unique(df['step'])
        f1 = figure(y_axis_type=y_axis_type, x_axis_type=x_axis_type, title='Time Sweep', tooltips=[('x','$x'),('y','$y')], width=500, height=400, y_range=(10,300))
        cmap, cmap_line = magma(np.size(steps)+1), darken(magma(np.size(steps)+1))

        for no, step in enumerate(steps): 
            # Check that step makes sense
            time_sweep = df[df['step'] == step]
            if time_sweep.iloc[0]['steptype'] != 'Timesweep':
                print('plot_tsweep > Cannot confirm that step ' + str(step) + ' is a time sweep')
            f1.scatter(x='time', y='gprime' , marker='square', line_color=cmap_line[no], fill_color=cmap[no], source=time_sweep, legend_label="Step " + str(step))
            f1.scatter(x='time', y='gsecond', marker='o',      line_color=cmap_line[no], fill_color='white', source=time_sweep)
            if plot_stress: 
                f1.scatter(x='frequency', y='stress', marker='triangle', line_color=cmap_line[no], fill_color=cmap[no], source=time_sweep)
                f1.xaxis.axis_label, f1.yaxis.axis_label = 't (s)', 'G, ?? (Pa)'
            else:
                f1.xaxis.axis_label, f1.yaxis.axis_label = 't (s)', 'G'', G" (Pa)'
    
        f1.legend.location = "right"

        show(f1)
        time.sleep(0.5)
        return f1
    else:
        print('plot_tsweep > No time sweep step in the sliced dataset')
        return None

def plot_startup(df, reversal=False, malvern=True, plot_vs_strain=False):
    """
    Function that helps plot shear startup and reversals

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - reversal [BOOL] [DEFAULT : FALSE] : bunch your startup data two-by-two and 
    consider that every odd (even) step is forward (reverse) done consecutively
    - malvern [BOOL] [DEFAULT : TRUE] : specify this to avoid issues with the shear
    stress not being negative during shear reversal tests.
    - plot_vs_strain [BOOL] [DEFAULT : FALSE] : plots stress vs. strain instead
    of strain vs. time

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        f1 = figure(title='Preshears', width=500, height=400)
        steps = np.unique(df['step'])
        strain_offset = np.zeros(len(steps) + 1)
        cmap = magma(np.size(steps)+1)

        # Fix things that are messed up first ...
        if reversal:
            steps_forward = steps[::2]
            steps_reverse = steps[1::2]
            if malvern:
                df = fix_stress_malvern_reversal(df, steps_reverse)
            else:
                df = fix_strain_antonpaar(df, steps) # XXX Will Fail with TA ...
        else:
            steps_forward = steps

        # Plot things, be careful with strain offsets
        for no, s in enumerate(steps):
            time = df[df['step'] == s]['time']
            strain, stress = df[df['step'] == s]['strain'], df[df['step'] == s]['stress']
            avgrate = np.mean(df[df['step'] == s]['shearrate'])
            if s in steps_forward and reversal: # No need for strain offsets if no reversal ...
                strain_offset[no + 1] = df[df['step'] == s]['strain'].iloc[-1]

            if plot_vs_strain:  
                f1.line(strain + strain_offset[no], stress, line_color=cmap[no], line_width=1, legend_label='Step ' + str(s))
                    # ' : ?? = ' + '{:2.1e}'.format(avgrate) + ' 1/s')
                
                f1.xaxis.axis_label, f1.yaxis.axis_label ='gamma (%)', 'sigma (Pa)'
                strslim, strnlim = np.max(np.abs(df['stress'])), np.max(np.abs(df['strain']))
                f1.line([0,strnlim], [0,0], line_dash='dotted', line_color='black')
                f1.line([strnlim/2, strnlim/2], [-strslim,strslim], line_dash='dotted', line_color='black')
            else:
                f1.line(time, stress, line_color=cmap[no], line_width=1, legend_label='Step ' + str(s))
                f1.xaxis.axis_label, f1.yaxis.axis_label ='t (s)', 'sigma (Pa)'

        f1.legend.location='bottom_right'
        show(f1)
        return f1
    else:
        print('plot_startup > No startup step in the sliced dataset')
        return None

def plot_control_startup(df, malvern=True, strain_as_x=True):
    """
        A companion function to check if your shear startup / reversals make sense
        Plots the strain rate as a function of time. Since I don't have the target
        I can't do just an "error plot" 

        ARGS : 
        - df [PANDAS.DATAFRAME] : your sliced rheology data. Note : you need to run "

        OUTPUT :
        - a figure (d'uh !)
    """
    
    f1 = figure(title='Strain rate control for reversal ...', y_axis_type='log')
    steps = np.unique(df['step'])
    cmap = magma(np.size(steps)+1)

    # Fix things that are messed up first ...
    if not malvern:
        df = fix_strain_antonpaar(df, steps)

    # Plot things, be careful with strain offsets
    for no, s in enumerate(steps):
        if strain_as_x:
            xdata = np.abs(df[df['step'] == s]['strain'])
            f1.xaxis.axis_label = '|gamma| (%)'
        else:
            xdata = df[df['step'] == s]['time']
            f1.xaxis.axis_label = 't (s)'

        rate = np.convolve(np.abs(df[df['step'] == s]['shearrate']), np.ones(5)/5, mode='same')
        est_rate = np.abs(np.mean(rate[-30:]))
            
        f1.line(xdata, rate, line_color=cmap[no], line_width=1)
        f1.line([0,np.max(xdata)], [est_rate,est_rate], line_color=cmap[no], line_dash='dashed')

    show(f1)
    return f1


    
def plot_creep(df, x_axis_type='log', y_axis_type='log', plot_shearrate = False):
    """
    Function that plots creep data

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - y_axis_type [STR] [DEFAULT : 'log'] : whether y axis is log or lin
    - x_axis_type [STR] [DEFAULT : 'log'] : whether x axis is log or lin
    - plot_shearrate [BOOL] [DEFAULT : False] : whether to plot gamma dot instead of gamma

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        f1 = figure(title='Creep', x_axis_type=x_axis_type, y_axis_type=y_axis_type)
        steps = np.unique(df['step'])
        cmap = magma(np.size(steps)+1)

        for no, cr in enumerate(steps):
            strn = df.loc[df['step'] == cr, 'strain']
            sr   = df.loc[df['step'] == cr, 'shearrate']
            tm = df.loc[df['step'] == cr, 'time']
            strs = df.loc[df['step'] == cr, 'stress'].to_numpy().mean()
            
            if plot_shearrate:
                f1.line(tm, sr, line_color=cmap[no], line_width=1, legend_label='Step ' + str(cr) + ', sigma = ' +  '{:4.2f}'.format(strs)  + ' Pa')
                f1.yaxis.axis_label = 'gamma^dot (1/s)'
                f1.legend.location = 'bottom_left'
            else:
                f1.line(tm, strn, line_color=cmap[no], line_width=1, legend_label='Step ' + str(cr) + ', sigma = ' + '{:4.2f}'.format(strs) + ' Pa')
                f1.line([1e-3,np.max(strn)],[1e-3,np.max(strn)], line_dash='dotted', line_color='black')
                f1.yaxis.axis_label = 'gamma (%)'
                f1.legend.location = 'top_left'

        f1.xaxis.axis_label = 'time (s)'
        show(f1)
        return f1
    else:
        print('plot_startup > No startup step in the sliced dataset')
        return None

    
def plot_stepstrain(df, x_axis_type='log', y_axis_type='log', plot_strain=True):
    """
    Function that plots step strain data

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - y_axis_type [STR] [DEFAULT : 'log'] : whether y axis is log or lin
    - x_axis_type [STR] [DEFAULT : 'log'] : whether x axis is log or lin
    - plot_strain [BOOL] [DEFAULT : False] : whether to check if gamma is well "enforced" during step strain

    OUTPUT :
    - a figure (d'uh !)
    """
    if len(df) > 0 and 'step' in df.keys():
        f1 = figure(title='Step Strain : Stress', x_axis_type=x_axis_type, y_axis_type=y_axis_type, width=750, height=350)
        f2 = figure(title='Step Strain : Strain', x_axis_type=x_axis_type, y_axis_type=y_axis_type, width=750, height=200)
        steps = np.unique(df['step'])
        cmap = magma(np.size(steps))
        cmap_d = darken(magma(np.size(steps)), factor=0.8)

        for no, cr in enumerate(steps):
            strn_t = df.loc[df['step'] == cr, 'strain']
            strn = df.loc[df['step'] == cr, 'strain'].iloc[-1]
            tm = df.loc[df['step'] == cr, 'time']
            strs = df.loc[df['step'] == cr, 'stress']
            
            if plot_strain:
                f1.line(tm, strs, line_color=cmap_d[no], line_width=1.5, legend_label='Step ' + str(cr) + ', ?? = ' + '{:4.2f}'.format(strn))
                f2.line(tm, strn_t, line_color=cmap_d[no], line_width=1.5)
                f2.line([1e-3,np.max(tm)],[strn, strn], line_dash='dotted', line_color='black')
            else:
                f1.line(tm, strs, line_color=cmap[no], line_width=1, legend_label='Step ' + str(cr) + ', ?? = ' +  '{:4.2f}'.format(strn))

        f2.x_range.start, f2.y_range.start=0.01, 0
        f1.x_range.start, f1.y_range.start=0.01, -0.1      
        f1.y_range.end = 10
        f1.legend.location = 'bottom_left'

        if plot_strain:
            f1.yaxis.axis_label = '?? (Pa)'
            f1.xaxis.axis_label = 't  (s)'
            f1.legend.location = 'top_right'
            f2.yaxis.axis_label = '?? (1)'
            f2.xaxis.axis_label = 't (s)'
            show(column(f1,f2))
        else:
            f1.legend.location = 'top_right'   
            f1.yaxis.axis_label = '?? (Pa)'
            f1.xaxis.axis_label = 't (s)'
            show(f1)


        return (f1, f2)
    else:
        print('plot_stepstrain > No step strain "step" in the sliced dataset')
        return None, None

    
def plot_stepstrain_normalised(df, x_axis_type='log', y_axis_type='log'):
    """
    Function that plots step strain data

    ARGS : 
    - df [PANDAS.DATAFRAME] : your sliced rheology data
    - y_axis_type [STR] [DEFAULT : 'log'] : whether y axis is log or lin
    - x_axis_type [STR] [DEFAULT : 'log'] : whether x axis is log or lin
    - plot_strain [BOOL] [DEFAULT : False] : whether to check if gamma is well "enforced" during step strain

    OUTPUT :
    - a figure (d'uh !)
    """

    if len(df) > 0 and 'step' in df.keys():
   
        f1 = figure(title='Step Strain : Stress', x_axis_type=x_axis_type, y_axis_type=y_axis_type, width=600, height=500)
        steps = np.unique(df['step'])
        cmap = magma(np.size(steps))
        cmap_d = darken(magma(np.size(steps)), factor=0.8)

        for no, cr in enumerate(steps):
            strn_t = df.loc[df['step'] == cr, 'strain']
            strn = df.loc[df['step'] == cr, 'strain'].iloc[-1]
            tm = df.loc[df['step'] == cr, 'time']
            strs = df.loc[df['step'] == cr, 'stress']
            strs0 = np.mean(strs[(tm < 1) & (tm > 0.5)])
            f1.line(tm, strs/strs0, line_color=cmap_d[no], line_width=1.5, legend_label='Step ' + str(cr) + ', ?? = ' + '{:4.2f}'.format(strn))
        
        f1.x_range.start, f1.y_range.start = 1, -0.1      
        f1.y_range.end, f1.y_range.start = 1, 0.009
        f1.legend.location = 'bottom_left'   
        f1.yaxis.axis_label = '?? / ??_0 '
        f1.xaxis.axis_label = 't (s)'
        show(f1)

        return (f1)
    else:
        print('plot_stepstrain_normalised > No step strain "step" in the sliced dataset')
        return None, None
