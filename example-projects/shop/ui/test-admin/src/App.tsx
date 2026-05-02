import {
  Admin,
  Resource,
  List,
  Datagrid,
  TextField,
  EmailField,
  NumberField,
  DateField,
  ReferenceField,
  ReferenceArrayField,
  SingleFieldList,
  ChipField,
  Show,
  SimpleShowLayout,
  Create,
  SimpleForm,
  TextInput,
  NumberInput,
  ReferenceInput,
  ReferenceArrayInput,
  EditGuesser,
} from 'react-admin';
import { Layout } from "./Layout";
import simpleRestProvider from 'ra-data-simple-rest';

const dataProvider = simpleRestProvider('http://localhost:8001');

// ---------------------------------------------------------------------------
// Customers
// ---------------------------------------------------------------------------

const CustomerList = () => (
  <List>
    <Datagrid rowClick="show">
      <TextField source="id" />
      <EmailField source="email" />
      <ReferenceArrayField source="orders" reference="orders" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="id" />
        </SingleFieldList>
      </ReferenceArrayField>
    </Datagrid>
  </List>
);

const CustomerShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="id" />
      <EmailField source="email" />
      <ReferenceArrayField source="orders" reference="orders" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="id" />
        </SingleFieldList>
      </ReferenceArrayField>
    </SimpleShowLayout>
  </Show>
);

const CustomerCreate = () => (
  <Create>
    <SimpleForm>
      <TextInput source="email" />
    </SimpleForm>
  </Create>
);

const CustomerEdit = () => (
  <Edit>
    <SimpleForm>
      <TextInput source="email" />
      <ReferenceArrayInput source="orders" reference="orders" />
    </SimpleForm>
  </Edit>
);

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------

const ProductList = () => (
  <List>
    <Datagrid rowClick="show">
      <TextField source="id" />
      <TextField source="name" />
      <NumberField source="price" />
      <ReferenceArrayField source="orders" reference="orders" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="id" />
        </SingleFieldList>
      </ReferenceArrayField>
    </Datagrid>
  </List>
);

const ProductShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="id" />
      <TextField source="name" />
      <NumberField source="price" />
      <ReferenceArrayField source="orders" reference="orders" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="id" />
        </SingleFieldList>
      </ReferenceArrayField>
    </SimpleShowLayout>
  </Show>
);

const ProductCreate = () => (
  <Create>
    <SimpleForm>
      <TextInput source="name" />
      <NumberInput source="price" />
    </SimpleForm>
  </Create>
);

// ---------------------------------------------------------------------------
// Orders
// ---------------------------------------------------------------------------

const OrderList = () => (
  <List>
    <Datagrid rowClick="show">
      <TextField source="id" />
      <ReferenceField source="customer_id" reference="customers" />
      <ReferenceArrayField source="products" reference="products" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="name" />
        </SingleFieldList>
      </ReferenceArrayField>
      <DateField source="created_at" showTime />
    </Datagrid>
  </List>
);

const OrderShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="id" />
      <ReferenceField source="customer_id" reference="customers" />
      <ReferenceArrayField source="products" reference="products" sortable={false}>
        <SingleFieldList linkType="show">
          <ChipField source="name" />
        </SingleFieldList>
      </ReferenceArrayField>
      <DateField source="created_at" showTime />
      <DateField source="updated_at" showTime />
    </SimpleShowLayout>
  </Show>
);

const OrderCreate = () => (
  <Create>
    <SimpleForm>
      <ReferenceInput source="customer_id" reference="customers" />
      <ReferenceArrayInput source="products" reference="products" />
    </SimpleForm>
  </Create>
);

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export const App = () => (
  <Admin layout={Layout} dataProvider={dataProvider}>
    <Resource name="customers" list={CustomerList} create={CustomerCreate} edit={EditGuesser} show={CustomerShow} />
    <Resource name="products"  list={ProductList}  create={ProductCreate}  edit={EditGuesser} show={ProductShow} />
    <Resource name="orders"    list={OrderList}    create={OrderCreate}    edit={EditGuesser} show={OrderShow} />
  </Admin>
);
