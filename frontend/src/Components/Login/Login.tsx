import { useForm } from 'react-hook-form'
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '../ui/form';
import { InputGroup, InputGroupAddon, InputGroupInput } from '../ui/input-group';
import { zodResolver } from '@hookform/resolvers/zod';
import { Input } from '../ui/input';
import { Schema, type LoginSchemaType } from './login.schema';
import { Button } from '../ui/button';
import { supabase } from '@/supabase-client';
import { useNavigate } from 'react-router-dom';
import { listDatasets } from '../../api';
import "../Register/register.css"
// import type { RegisterBodyType } from './schemaType';
import bg from '../../assets/bg1.jpg'
import AxBiLogo from '../ui/AxBiLogo'
import toast from 'react-hot-toast';
import { useState } from 'react';
export default function Login() {
  const login = useForm<LoginSchemaType>({
    resolver: zodResolver(Schema),
    defaultValues: {
      email: '',
      password: '',
    },
    mode: 'onBlur',
  })
  const navigate = useNavigate();
  const { control, handleSubmit } = login;
  const [isLogging, setIsLogging] = useState(false);
  const onSubmit = async (data: LoginSchemaType) => {
    const { email, password } = data
    setIsLogging(true)

    const { data: authData, error } =
      await supabase.auth.signInWithPassword({
        email,
        password,
      })

    if (error) {
      console.error(error.message)
      setIsLogging(false)
      toast.error(error.message)
      return
    }
    toast.success('Logged in successfully!')

    // Send first-time users (no projects yet) into the guided wizard; everyone
    // else lands on the AI agent. Default to the agent if the check fails.
    try {
      const res = await listDatasets()
      navigate((res?.datasets?.length ?? 0) === 0 ? '/onboarding' : '/agent')
    } catch {
      navigate('/agent')
    }
    setIsLogging(false)
    console.log('Logged in user:', authData.user)
  }


  return (
    <div className='grid lg:grid-cols-2 register'>
      <div className='lg:flex flex-col justify-center h-screen   p-10 gap-10 text-white hidden max-w-[600px] mx-auto '>
        <h2 className='caret-transparent'><AxBiLogo className='h-12' forceTheme='dark' /></h2>
        <h1 className='text-6xl font-extrabold  caret-transparent'>Intelligent <br /> Insights for <br /> Modern <br /> Enterprises</h1>
        <p className='text-white/80 '>Harness the power of Al-driven analytics to transform your raw data into strategic business decisions</p>
      </div>
      <Form {...login}  >
        <form className=' bg-card' onSubmit={handleSubmit(onSubmit)}>
          <div className=' h-screen flex items-center'>
            <div className='grid grid-cols-1 sm:grid-cols-2 gap-3 p-10 max-w-[600px] mx-auto'>
              <div className='sm:col-span-2'>
                <h2 className='text-4xl font-bold text-foreground my-3' >Welcome Back</h2>
                <p className='text-muted-foreground my-3'>Enter your credentials to access the Al-powered dashboard.</p>
              </div>

              <FormField
                control={control}
                name="email"
                render={({ field, fieldState }) => (
                  <FormItem className='dark:text-gray-700 sm:col-span-2' >
                    <FormLabel>
                      Email
                    </FormLabel>
                    <FormControl>
                      <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                        <InputGroupInput type='email' aria-invalid={fieldState.invalid} {...field} placeholder='name@company.com' />
                        <InputGroupAddon>
                          <i className="text-lg fa-solid fa-envelope"></i>
                        </InputGroupAddon>
                      </InputGroup>
                    </FormControl>
                    <FormMessage className='font-semibold' />
                  </FormItem>
                )}
              />

              <FormField
                control={control}
                name="password"
                render={({ field, fieldState }) => (
                  <FormItem className='dark:text-gray-700 sm:col-span-2'>
                    <FormLabel >
                      Password
                    </FormLabel>
                    <FormControl>
                      { /* Your form field */}
                      <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                        <InputGroupInput type='password' aria-invalid={fieldState.invalid} {...field} placeholder="••••••••" />
                        <InputGroupAddon>
                          <i className="text-lg fa-solid fa-lock"></i>
                        </InputGroupAddon>
                      </InputGroup>
                    </FormControl>
                    <FormMessage className='font-semibold' />
                  </FormItem>
                )}
              />

              <div className='sm:col-span-2'>
                <button disabled={isLogging} type="submit" className='bg-primary hover:bg-primary duration-300 cursor-pointer rounded-3xl py-3  w-full text-lg text-primary-foreground font-semibold my-5'>{isLogging ? "Signing In" : "Login"} <i className="fa-solid fa-arrow-right"></i></button>
                <p className='text-muted-foreground  text-center'>Don't have an account?<a onClick={() => navigate('/register')} className='text-primary hover:opacity-80 duration-300 font-bold cursor-pointer'> register</a></p>
              </div>
            </div>
          </div>
        </form>

      </Form>

    </div>

  )
}
