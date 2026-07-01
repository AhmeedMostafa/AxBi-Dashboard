import { useForm } from 'react-hook-form'
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '../ui/form';
import { Input } from '../ui/input';
import { zodResolver } from '@hookform/resolvers/zod';
import { Schema } from './register.schema';
import type { RegisterBodyType } from './schemaType';
import { Button } from '../ui/button';
import { useNavigate } from 'react-router-dom';
import { supabase } from '@/supabase-client';
import { toast } from 'react-hot-toast';
import { InputGroup, InputGroupAddon, InputGroupInput } from '../ui/input-group';
import AxBiLogo from '../ui/AxBiLogo'
import './register.css'

export default function Register() {


    const RHFObj = useForm({
        resolver: zodResolver(Schema),
        defaultValues: {
            name: '',
            email: '',
            password: '',
            confirmPassword: '',
            companyName: '',
            industrialField: ''
        },
        mode: 'onBlur'
    });
    const { control, handleSubmit } = RHFObj;
    const navigate = useNavigate();

    const register = async (formData: RegisterBodyType) => {
        const {
            name,
            companyName,
            industrialField,
            email,
            password,
            confirmPassword,
        } = formData

        if (password !== confirmPassword) {
            toast.error("Passwords do not match")
            return;
        }
        try {

            const { data: authData, error: signUpError } =
                await supabase.auth.signUp({
                    email,
                    password,
                    options: {
                        data: {
                            name,
                            company_name: companyName,
                            industrial_field: industrialField,
                        },
                    },
                })
            console.log(authData);
            console.log(signUpError)
            if (signUpError) {
                // throw signUpError
                toast.error(signUpError.message)
                return;
            }
            if (!authData.user) {
                throw new Error("User not created")
                toast.error("User not created")
            }
            toast.success("Account created successfully 🎉")
            // If email confirmation is disabled, Supabase returns an active
            // session — send the user straight into the project wizard.
            if (authData.session) {
                navigate("/onboarding")
            } else {
                navigate("/login")
            }
        } catch (error: any) {
            toast.error(error.message || "Registration failed")
            return;
        }
    }


    return (
        <div className='grid lg:grid-cols-2 register'>
            <div className='lg:flex flex-col justify-center h-screen   p-10 gap-10 text-white hidden max-w-[600px] mx-auto '>
                <h2 className='caret-transparent'><AxBiLogo className='h-12' forceTheme='dark' /></h2>
                <h1 className='text-6xl font-extrabold  caret-transparent'>Intelligent <br /> Insights for <br /> Modern <br /> Enterprises</h1>
                <p className='text-white/80 '>Harness the power of Al-driven analytics to transform your raw data into strategic business decisions</p>
            </div>

            <Form {...RHFObj}  >
                <form className=' bg-card' onSubmit={handleSubmit(register)}>
                    <div className='grid grid-cols-1 sm:grid-cols-2 gap-3 p-10 max-w-[600px] mx-auto h-screen'>

                        <div className='sm:col-span-2'>
                            <h2 className='text-4xl font-bold text-foreground my-3' >Create your account</h2>
                            <p className='text-muted-foreground my-3'>Complete the details below to set up your professional profile.</p>
                        </div>

                        <FormField
                            control={control}
                            name="name"
                            render={({ field, fieldState }) => (
                                <FormItem className='dark:text-gray-700'>
                                    <FormLabel>
                                        Name
                                    </FormLabel>
                                    <div className='relative'>
                                        <FormControl>
                                            <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                                                <InputGroupInput type='text' aria-invalid={fieldState.invalid}  {...field} placeholder='Jone Doe' />
                                                <InputGroupAddon>
                                                    <i className="text-lg fa-solid fa-user"></i>
                                                </InputGroupAddon>
                                            </InputGroup>
                                        </FormControl>
                                    </div>
                                    <FormMessage className='font-semibold' />
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={control}
                            name="email"
                            render={({ field, fieldState }) => (
                                <FormItem className='dark:text-gray-700' >
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
                            name="companyName"
                            render={({ field, fieldState }) => (
                                <FormItem className='dark:text-gray-700' >
                                    <FormLabel>
                                        Company Name
                                    </FormLabel>
                                    <FormControl>
                                        <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                                            <InputGroupInput type='text' aria-invalid={fieldState.invalid} {...field} placeholder='Acme Corp' />
                                            <InputGroupAddon>
                                                <i className="text-lg fa-solid fa-briefcase"></i>
                                            </InputGroupAddon>
                                        </InputGroup>
                                    </FormControl>

                                    <FormMessage className='font-semibold' />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={control}
                            name="industrialField"
                            render={({ field, fieldState }) => (
                                <FormItem className='dark:text-gray-700' >
                                    <FormLabel>
                                        Industrial Field
                                    </FormLabel>
                                    <FormControl>
                                        <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                                            <InputGroupInput type='text' aria-invalid={fieldState.invalid} {...field} placeholder='Digital Media' />
                                            <InputGroupAddon>
                                                <i className="text-lg fa-solid fa-industry"></i>
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
                        <FormField
                            control={control}
                            name="confirmPassword"
                            render={({ field, fieldState }) => (
                                <FormItem className='dark:text-gray-700 sm:col-span-2'>
                                    <FormLabel >
                                        Confirm Password
                                    </FormLabel>
                                    <FormControl>
                                        <InputGroup className=' text-sm bg-white rounded-3xl py-6 decoration-0 dark:bg-muted' >
                                            <InputGroupInput type='password' aria-invalid={fieldState.invalid} {...field} placeholder="••••••••" />
                                            <InputGroupAddon>
                                                <i className="text-lg fa-solid fa-circle-check"></i>
                                            </InputGroupAddon>
                                        </InputGroup>
                                    </FormControl>
                                    <FormMessage className='font-semibold' />
                                </FormItem>
                            )}
                        />
                        <div className='sm:col-span-2'>
                            <button className='bg-primary hover:bg-primary duration-300 cursor-pointer rounded-3xl py-3  w-full text-lg text-primary-foreground font-semibold my-5'>Create Account <i className="fa-solid fa-arrow-right"></i></button>
                            <p className='text-muted-foreground  text-center'>Already have an account?<a onClick={() => navigate('/login')} className='text-primary hover:opacity-80 duration-300 font-bold cursor-pointer'> Login</a></p>
                        </div>
                    </div>
                </form>

            </Form>
        </div>
    )
}
